from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db_session import get_db
from src.models.core import AuditLog, ClientInstallation, MidiaSimplesSessionCache, User
from src.services.midiasimples.session_store import get_validated_session
from src.services.technicians import get_technician_by_email


router = APIRouter(prefix="/admin/hub", tags=["Painel Backend"])
SP_TZ = ZoneInfo("America/Sao_Paulo")


def _require_admin_access(request: Request, authorization: str | None, token: str | None) -> None:
    client_host = request.client.host if request.client else ""
    if client_host in {"127.0.0.1", "::1", "localhost"}:
        return
    if not settings.sync_ingest_token:
        return
    expected = f"Bearer {settings.sync_ingest_token}"
    if authorization == expected or token == settings.sync_ingest_token:
        return
    raise HTTPException(status_code=401, detail="Nao autenticado")


def _as_sp(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SP_TZ).isoformat(timespec="seconds")


def _hotfix_manifest() -> dict[str, Any] | None:
    path = Path.cwd() / "data" / "hotfix" / "manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"error": f"Manifesto invalido: {exc}"}


def _session_item(row: MidiaSimplesSessionCache, *, validate: bool) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    last_used = row.last_used_at.replace(tzinfo=timezone.utc) if row.last_used_at else None
    expires_at = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at else None
    expired = bool(expires_at and expires_at < now)
    validation = "not_checked"

    if row.status == "ativa" and not expired and validate:
        try:
            validation = "valid" if get_validated_session(row.email, request_timeout=8) else "invalid"
        except Exception as exc:
            validation = f"error:{type(exc).__name__}"

    technician = get_technician_by_email(row.email)
    return {
        "id": row.id,
        "email": row.email,
        "name": technician.display_name if technician else row.user_name,
        "status": row.status,
        "validity": "expired" if expired else "within_window",
        "validation": validation,
        "last_used_at": _as_sp(last_used),
        "expires_at": _as_sp(expires_at),
        "base_url": row.base_url,
    }


def _client_item(row: ClientInstallation) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    last_seen = row.last_seen_at.replace(tzinfo=timezone.utc) if row.last_seen_at else None
    age_minutes = ((now - last_seen).total_seconds() / 60) if last_seen else None
    return {
        "id": row.id,
        "client_id": row.client_id,
        "hostname": row.hostname,
        "windows_user": row.windows_user,
        "technician_email": row.technician_email,
        "technician_name": row.technician_name,
        "app_version": row.app_version,
        "backend_version": row.backend_version,
        "install_mode": row.install_mode,
        "last_ip": row.last_ip,
        "status": row.status,
        "last_seen_at": _as_sp(last_seen),
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "presence": "online" if age_minutes is not None and age_minutes <= 15 else "offline",
    }


def _audit_item(row: AuditLog, users_by_id: dict[int, User]) -> dict[str, Any]:
    user = users_by_id.get(row.usuario_id or 0)
    payload = row.payload or {}
    actor_email = payload.get("actor_email") if isinstance(payload, dict) else None
    return {
        "id": row.id,
        "created_at": _as_sp(row.created_at),
        "usuario_id": row.usuario_id,
        "user": user.apelido or user.nome if user else actor_email,
        "email": user.email if user else actor_email,
        "acao": row.acao,
        "modulo": row.modulo,
        "resultado": row.resultado,
        "erro": row.erro,
        "payload": payload,
    }


@router.get("/status")
def hub_status(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    validate_sessions: bool = Query(default=False),
) -> dict[str, Any]:
    _require_admin_access(request, authorization, token)

    sessions = (
        db.query(MidiaSimplesSessionCache)
        .order_by(MidiaSimplesSessionCache.last_used_at.desc().nullslast(), MidiaSimplesSessionCache.created_at.desc())
        .all()
    )
    clients = (
        db.query(ClientInstallation)
        .order_by(ClientInstallation.last_seen_at.desc().nullslast(), ClientInstallation.created_at.desc())
        .limit(200)
        .all()
    )
    audit_rows = db.query(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(100).all()
    users_by_id = {user.id: user for user in db.query(User).all()}

    session_items = [_session_item(row, validate=validate_sessions) for row in sessions]
    client_items = [_client_item(row) for row in clients]

    return {
        "status": "ok",
        "checked_at": datetime.now(SP_TZ).isoformat(timespec="seconds"),
        "app": {
            "name": settings.app_name,
            "version": settings.app_version,
            "env": settings.app_env,
            "server_host": settings.server_host,
            "server_port": settings.server_port,
        },
        "hotfix": _hotfix_manifest(),
        "sessions": {
            "total": len(session_items),
            "active_window": sum(1 for item in session_items if item["status"] == "ativa" and item["validity"] == "within_window"),
            "validated": sum(1 for item in session_items if item["validation"] == "valid"),
            "items": session_items,
        },
        "clients": {
            "total": len(client_items),
            "online": sum(1 for item in client_items if item["presence"] == "online"),
            "items": client_items,
        },
        "audit": {
            "total_returned": len(audit_rows),
            "items": [_audit_item(row, users_by_id) for row in audit_rows],
        },
    }


@router.get("", response_class=HTMLResponse)
def hub_panel(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    _require_admin_access(request, authorization, token)
    token_query = f"?token={token}" if token else ""
    status_url = f"/admin/hub/status{token_query}"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Painel Backend HUB</title>
  <style>
    :root {{ color-scheme: light; font-family: Arial, sans-serif; }}
    body {{ margin: 0; background: #f5f7fb; color: #162033; }}
    header {{ padding: 20px 28px; background: #102033; color: #fff; }}
    main {{ padding: 22px 28px; max-width: 1400px; margin: 0 auto; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; }}
    h2 {{ margin: 26px 0 10px; font-size: 18px; }}
    .meta {{ color: #cbd5e1; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .card {{ background: #fff; border: 1px solid #d8dee9; border-radius: 8px; padding: 14px; }}
    .label {{ color: #64748b; font-size: 12px; text-transform: uppercase; }}
    .value {{ font-size: 22px; font-weight: 700; margin-top: 6px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dee9; }}
    th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid #e5e7eb; font-size: 13px; vertical-align: top; }}
    th {{ background: #edf2f7; color: #334155; position: sticky; top: 0; }}
    code {{ background: #eef2f7; padding: 2px 4px; border-radius: 4px; }}
    .ok {{ color: #047857; font-weight: 700; }}
    .warn {{ color: #b45309; font-weight: 700; }}
    .bad {{ color: #b91c1c; font-weight: 700; }}
    .table-wrap {{ overflow: auto; max-height: 420px; border-radius: 8px; }}
  </style>
</head>
<body>
  <header>
    <h1>Painel Backend HUB</h1>
    <div class="meta" id="meta">Carregando...</div>
  </header>
  <main>
    <section class="grid" id="cards"></section>
    <h2>Usuários Logados</h2>
    <div class="table-wrap"><table id="sessions"></table></div>
    <h2>Clientes Desktop</h2>
    <div class="table-wrap"><table id="clients"></table></div>
    <h2>Auditoria Recente</h2>
    <div class="table-wrap"><table id="audit"></table></div>
  </main>
  <script>
    const statusUrl = {json.dumps(status_url + ("&" if token_query else "?") + "validate_sessions=true")};
    const td = (v) => `<td>${{v ?? ""}}</td>`;
    const statusClass = (v) => v === "valid" || v === "online" || v === "ok" ? "ok" : (String(v || "").includes("expired") || v === "offline" ? "warn" : "");
    function table(el, headers, rows) {{
      el.innerHTML = `<thead><tr>${{headers.map(h => `<th>${{h}}</th>`).join("")}}</tr></thead><tbody>${{rows.join("")}}</tbody>`;
    }}
    async function load() {{
      const res = await fetch(statusUrl, {{ cache: "no-store" }});
      const data = await res.json();
      document.getElementById("meta").textContent = `${{data.app.name}} ${{data.app.version}} · ${{data.app.env}} · Atualizado em ${{data.checked_at}}`;
      const hotfix = data.hotfix || {{}};
      document.getElementById("cards").innerHTML = [
        ["Versão Backend", data.app.version],
        ["Hotfix publicado", hotfix.version || "-"],
        ["Sessões válidas", `${{data.sessions.validated}} / ${{data.sessions.total}}`],
        ["Clientes online", `${{data.clients.online}} / ${{data.clients.total}}`],
      ].map(([label, value]) => `<div class="card"><div class="label">${{label}}</div><div class="value">${{value}}</div></div>`).join("");
      table(document.getElementById("sessions"), ["Nome", "E-mail", "Status", "Validação", "Último uso", "Expira"], data.sessions.items.map(s =>
        `<tr>${{td(s.name)}}${{td(`<code>${{s.email}}</code>`)}}${{td(s.status)}}<td class="${{statusClass(s.validation)}}">${{s.validation}}</td>${{td(s.last_used_at)}}${{td(s.expires_at)}}</tr>`
      ));
      table(document.getElementById("clients"), ["Host", "Windows", "Técnico", "Versão", "IP", "Presença", "Último sinal"], data.clients.items.map(c =>
        `<tr>${{td(c.hostname)}}${{td(c.windows_user)}}${{td(c.technician_name || c.technician_email)}}${{td(c.app_version)}}${{td(c.last_ip)}}<td class="${{statusClass(c.presence)}}">${{c.presence}}</td>${{td(c.last_seen_at)}}</tr>`
      ));
      table(document.getElementById("audit"), ["Quando", "Usuário", "Ação", "Resultado", "Rota/IP"], data.audit.items.map(a => {{
        const p = a.payload || {{}};
        return `<tr>${{td(a.created_at)}}${{td(a.user || a.email)}}${{td(a.acao)}}${{td(a.resultado)}}${{td(`${{p.method || ""}} ${{p.path || ""}}<br><code>${{p.client_ip || ""}}</code>`)}}</tr>`;
      }}));
    }}
    load();
    setInterval(load, 30000);
  </script>
</body>
</html>"""
    )
