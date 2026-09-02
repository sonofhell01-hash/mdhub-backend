from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.core.db_session import get_db
from src.api.routes.hotfix import read_hotfix_manifest
from src.services.sync_queue import list_pending, queue_event, queue_summary, sync_once


router = APIRouter(prefix="/sync", tags=["Offline e Sync"])


class QueueEventRequest(BaseModel):
    tipo: str = Field(default="MANUAL_EVENT")
    payload: dict[str, Any] = Field(default_factory=dict)
    usuario_id: int | None = None


def _serialize_item(item):
    return {
        "id": item.id,
        "usuario_id": item.usuario_id,
        "tipo": item.tipo,
        "status": item.status,
        "tentativas": item.tentativas,
        "ultimo_erro": item.ultimo_erro,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "ultima_tentativa": item.ultima_tentativa.isoformat() if item.ultima_tentativa else None,
        "payload": item.payload,
    }


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return queue_summary(db)


@router.get("/pending")
def pending(
    limit: int = Query(50, ge=1, le=200),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    return {
        "items": [_serialize_item(item) for item in list_pending(db, limit=limit, status=status)],
        "summary": queue_summary(db),
    }


@router.post("/queue")
def queue(body: QueueEventRequest, db: Session = Depends(get_db)):
    item = queue_event(db, tipo=body.tipo, payload=body.payload, usuario_id=body.usuario_id)
    return {"status": "ok", "item": _serialize_item(item)}


@router.post("/run")
def run(limit: int = Query(25, ge=1, le=100), db: Session = Depends(get_db)):
    result = sync_once(db, limit=limit)
    hotfix_state = read_hotfix_manifest()
    hotfix = hotfix_state.get("hotfix") if hotfix_state.get("status") == "ok" else None
    client_update = hotfix.get("client_update") if isinstance(hotfix, dict) else None
    push_install = bool(
        hotfix
        and (
            client_update.get("available")
            if isinstance(client_update, dict)
            else hotfix.get("mandatory")
        )
        and hotfix.get("mandatory")
    )
    return {
        "status": "ok",
        "mode": result.mode,
        "processed": result.processed,
        "synced": result.synced,
        "failed": result.failed,
        "skipped": result.skipped,
        "update_available": bool(hotfix),
        "push_install": push_install,
        "hotfix": hotfix,
        "summary": queue_summary(db),
    }
