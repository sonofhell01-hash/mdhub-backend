from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
import json
import socket

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.config import settings
from src.models.core import AuditLog, SyncPending


SYNCABLE_TYPES = {
    "RAT_CREATED",
    "LAUDO_CREATED",
    "DEVOLUCAO_CREATED",
    "CONCESSAO_CREATED",
    "SUBSTITUICAO_CREATED",
    "EMPRESTIMO_CREATED",
    "MANUAL_EVENT",
}


@dataclass
class SyncResult:
    processed: int = 0
    synced: int = 0
    failed: int = 0
    skipped: int = 0
    mode: str = "local"


class HubUnavailableError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now()


def queue_event(
    db: Session,
    *,
    tipo: str,
    payload: dict[str, Any],
    usuario_id: int | None = None,
) -> SyncPending:
    if tipo not in SYNCABLE_TYPES:
        tipo = "MANUAL_EVENT"

    item = SyncPending(
        usuario_id=usuario_id,
        tipo=tipo,
        payload=payload,
        status="pendente",
    )
    db.add(item)
    db.add(
        AuditLog(
            usuario_id=usuario_id,
            acao="SYNC_QUEUED",
            modulo="sync",
            resultado="pendente",
            payload={"tipo": tipo, "payload": payload},
        )
    )
    db.commit()
    db.refresh(item)
    return item


def queue_summary(db: Session) -> dict[str, Any]:
    rows = (
        db.query(SyncPending.status, func.count(SyncPending.id))
        .group_by(SyncPending.status)
        .all()
    )
    by_status = {status: count for status, count in rows}
    return {
        "total": sum(by_status.values()),
        "pendente": by_status.get("pendente", 0),
        "sincronizado": by_status.get("sincronizado", 0),
        "erro": by_status.get("erro", 0),
        "by_status": by_status,
        "hub_configurado": bool(settings.sync_hub_url),
        "hub_url": settings.sync_hub_url,
    }


def list_pending(db: Session, limit: int = 50, status: str | None = None) -> list[SyncPending]:
    query = db.query(SyncPending)
    if status:
        query = query.filter(SyncPending.status == status)
    return query.order_by(SyncPending.created_at.desc()).limit(limit).all()


def _post_to_hub(item: SyncPending) -> dict[str, Any]:
    if not settings.sync_hub_url:
        raise RuntimeError("SYNC_HUB_URL nao configurado. Item mantido em contingencia local.")

    url = settings.sync_hub_url.rstrip("/") + "/api/ingest/events"
    body = json.dumps(
        {
            "event_id": item.id,
            "type": item.tipo,
            "payload": item.payload,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.sync_ingest_token:
        headers["Authorization"] = f"Bearer {settings.sync_ingest_token}"

    req = urllib_request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=5) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text else {}
            except json.JSONDecodeError:
                parsed = {"raw": text}
            return {"status_code": resp.status, "response": parsed}
    except HTTPError:
        raise
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise HubUnavailableError(
            f"HUB central indisponivel ({settings.sync_hub_url}). Item mantido em contingencia local."
        ) from exc


def sync_once(db: Session, limit: int = 25) -> SyncResult:
    result = SyncResult(mode="hub" if settings.sync_hub_url else "local_contingency")
    items = (
        db.query(SyncPending)
        .filter(SyncPending.status.in_(["pendente", "erro"]))
        .order_by(SyncPending.created_at.asc())
        .limit(limit)
        .all()
    )

    for item in items:
        result.processed += 1
        item.tentativas = (item.tentativas or 0) + 1
        item.ultima_tentativa = _now()

        if not settings.sync_hub_url:
            item.status = "sincronizado"
            item.ultimo_erro = None
            result.synced += 1
            db.add(
                AuditLog(
                    usuario_id=item.usuario_id,
                    acao="SYNC_LOCAL_ACK",
                    modulo="sync",
                    resultado="ok",
                    payload={
                        "sync_id": item.id,
                        "tipo": item.tipo,
                        "reason": "SYNC_HUB_URL vazio; evento ja esta registrado no banco local/central.",
                    },
                )
            )
            continue

        try:
            response = _post_to_hub(item)
            item.status = "sincronizado"
            item.ultimo_erro = None
            result.synced += 1
            db.add(
                AuditLog(
                    usuario_id=item.usuario_id,
                    acao="SYNC_SENT",
                    modulo="sync",
                    resultado="ok",
                    payload={"sync_id": item.id, "tipo": item.tipo},
                    resposta=response,
                )
            )
        except HubUnavailableError as exc:
            result.mode = "local_contingency"
            item.status = "pendente"
            item.ultimo_erro = str(exc)
            result.skipped += 1
            db.add(
                AuditLog(
                    usuario_id=item.usuario_id,
                    acao="SYNC_FAILED",
                    modulo="sync",
                    resultado="contingencia",
                    payload={"sync_id": item.id, "tipo": item.tipo},
                    erro=str(exc),
                )
            )
        except RuntimeError as exc:
            result.mode = "local_contingency"
            item.status = "pendente"
            item.ultimo_erro = str(exc)
            result.skipped += 1
            db.add(
                AuditLog(
                    usuario_id=item.usuario_id,
                    acao="SYNC_FAILED",
                    modulo="sync",
                    resultado="contingencia",
                    payload={"sync_id": item.id, "tipo": item.tipo},
                    erro=str(exc),
                )
            )
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            item.status = "erro"
            item.ultimo_erro = str(exc)
            result.failed += 1
            db.add(
                AuditLog(
                    usuario_id=item.usuario_id,
                    acao="SYNC_FAILED",
                    modulo="sync",
                    resultado="erro",
                    payload={"sync_id": item.id, "tipo": item.tipo},
                    erro=str(exc),
                )
            )

    db.commit()
    return result
