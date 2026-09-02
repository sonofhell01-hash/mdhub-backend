from __future__ import annotations

import json
import time
from typing import Any

from fastapi import Request

from src.core.db_session import SessionLocal
from src.models.core import AuditLog, User


SENSITIVE_KEYS = {
    "password",
    "senha",
    "token",
    "access_token",
    "authorization",
    "cookie",
    "cookies",
    "session",
    "session_data",
    "auth",
    "secret",
    "security_key",
    "api_key",
}

AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SKIPPED_PREFIXES = (
    "/admin/hub/status",
    "/docs",
    "/openapi.json",
    "/redoc",
)


def should_audit_request(request: Request) -> bool:
    if request.method.upper() not in AUDITED_METHODS:
        return False
    path = request.url.path
    return not any(path.startswith(prefix) for prefix in SKIPPED_PREFIXES)


def sanitize_payload(value: Any, *, max_string: int = 500, max_items: int = 30) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                clean["_truncated"] = True
                break
            lowered = str(key).lower()
            if any(sensitive in lowered for sensitive in SENSITIVE_KEYS):
                clean[str(key)] = "***"
            else:
                clean[str(key)] = sanitize_payload(item, max_string=max_string, max_items=max_items)
        return clean
    if isinstance(value, list):
        items = [sanitize_payload(item, max_string=max_string, max_items=max_items) for item in value[:max_items]]
        if len(value) > max_items:
            items.append({"_truncated": True})
        return items
    if isinstance(value, str):
        return value if len(value) <= max_string else value[:max_string] + "...[truncated]"
    return value


def decode_json_body(raw_body: bytes) -> Any:
    if not raw_body:
        return None
    try:
        return json.loads(raw_body.decode("utf-8"))
    except Exception:
        return {"_raw_size": len(raw_body), "_content": raw_body[:300].decode("utf-8", errors="replace")}


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _nested_get(payload: Any, *path: str) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def infer_actor(payload: Any, request: Request) -> tuple[int | None, str | None]:
    email = _first_text(
        _nested_get(payload, "email"),
        _nested_get(payload, "technician_email"),
        _nested_get(payload, "tecnico", "email"),
        _nested_get(payload, "payload", "tecnico", "email"),
        request.query_params.get("email"),
        request.headers.get("x-user-email"),
        request.headers.get("x-technician-email"),
    )

    raw_user_id = _first_text(
        _nested_get(payload, "usuario_id"),
        _nested_get(payload, "user_id"),
        _nested_get(payload, "payload", "usuario_id"),
        request.query_params.get("usuario_id"),
    )

    user_id: int | None = None
    if raw_user_id and raw_user_id.isdigit():
        user_id = int(raw_user_id)

    if user_id or not email:
        return user_id, email.lower() if email else None

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.lower()).first()
        return (user.id if user else None), email.lower()
    finally:
        db.close()


def audit_request(
    request: Request,
    *,
    raw_body: bytes,
    status_code: int,
    elapsed_ms: int,
    error: str | None = None,
) -> None:
    payload = decode_json_body(raw_body)
    user_id, actor_email = infer_actor(payload, request)
    client_host = request.client.host if request.client else None
    action = f"HTTP_{request.method.upper()}_{request.url.path.strip('/').replace('/', '_').replace('-', '_') or 'root'}"

    audit_payload = {
        "path": request.url.path,
        "method": request.method.upper(),
        "query": sanitize_payload(dict(request.query_params)),
        "actor_email": actor_email,
        "client_ip": client_host,
        "user_agent": request.headers.get("user-agent"),
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "body": sanitize_payload(payload),
    }

    db = SessionLocal()
    try:
        db.add(
            AuditLog(
                usuario_id=user_id,
                acao=action[:80],
                modulo="api",
                resultado="ok" if status_code < 400 else "erro",
                payload=audit_payload,
                erro=error,
            )
        )
        db.commit()
    finally:
        db.close()


async def audit_http_request(request: Request, call_next):
    if not should_audit_request(request):
        return await call_next(request)

    started = time.perf_counter()
    raw_body = await request.body()
    status_code = 500
    error: str | None = None

    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        try:
            audit_request(
                request,
                raw_body=raw_body,
                status_code=status_code,
                elapsed_ms=elapsed_ms,
                error=error,
            )
        except Exception:
            # Auditoria nunca deve derrubar uma operacao do HUB.
            pass
