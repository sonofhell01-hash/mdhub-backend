from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address, ip_network
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db_session import get_db
from src.models.core import ClientInstallation
from src.services.client_identity import local_machine_identity


router = APIRouter(prefix="/clients", tags=["Clientes Desktop"])


class ClientRegisterPayload(BaseModel):
    client_id: str
    hostname: str | None = None
    windows_user: str | None = None
    technician_email: str | None = None
    technician_name: str | None = None
    app_version: str | None = None
    backend_version: str | None = None
    install_mode: str | None = None
    payload: dict[str, Any] | None = None


def _require_ingest_token(authorization: str | None) -> None:
    if not settings.sync_ingest_token:
        return
    expected = f"Bearer {settings.sync_ingest_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Nao autenticado")


def _trusted_heartbeat_source(request: Request) -> bool:
    """Compatibilidade com clientes legados dentro da rede corporativa TIM."""
    if not request.client:
        return False
    try:
        source = ip_address(request.client.host)
    except ValueError:
        return False
    return source.is_loopback or source in ip_network("10.136.0.0/16")


def _upsert_client(body: ClientRegisterPayload, request: Request, db: Session) -> dict[str, Any]:
    now = datetime.now()
    item = db.query(ClientInstallation).filter_by(client_id=body.client_id).first()
    if not item:
        item = ClientInstallation(client_id=body.client_id)
        db.add(item)

    item.hostname = body.hostname
    item.windows_user = body.windows_user
    item.technician_email = body.technician_email
    item.technician_name = body.technician_name
    item.app_version = body.app_version
    item.backend_version = body.backend_version
    item.install_mode = body.install_mode or "desktop"
    item.last_ip = request.client.host if request.client else None
    item.status = "ativo"
    item.payload = body.payload
    item.last_seen_at = now
    item.updated_at = now

    db.commit()
    db.refresh(item)

    return {
        "status": "ok",
        "client": {
            "id": item.id,
            "client_id": item.client_id,
            "hostname": item.hostname,
            "windows_user": item.windows_user,
            "technician_email": item.technician_email,
            "technician_name": item.technician_name,
            "app_version": item.app_version,
            "last_ip": item.last_ip,
            "last_seen_at": item.last_seen_at.isoformat() if item.last_seen_at else None,
        },
    }


@router.get("/me")
def get_local_client_identity() -> dict[str, Any]:
    return local_machine_identity()


@router.post("/register")
def register_client(
    body: ClientRegisterPayload,
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_ingest_token(authorization)
    return _upsert_client(body, request, db)


@router.post("/heartbeat")
def heartbeat_client(
    body: ClientRegisterPayload,
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not _trusted_heartbeat_source(request):
        _require_ingest_token(authorization)
    return _upsert_client(body, request, db)


@router.get("")
def list_clients(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_ingest_token(authorization)
    rows = (
        db.query(ClientInstallation)
        .order_by(ClientInstallation.last_seen_at.desc().nullslast(), ClientInstallation.created_at.desc())
        .limit(200)
        .all()
    )
    return {
        "total": len(rows),
        "items": [
            {
                "id": item.id,
                "client_id": item.client_id,
                "hostname": item.hostname,
                "windows_user": item.windows_user,
                "technician_email": item.technician_email,
                "technician_name": item.technician_name,
                "app_version": item.app_version,
                "backend_version": item.backend_version,
                "install_mode": item.install_mode,
                "last_ip": item.last_ip,
                "status": item.status,
                "last_seen_at": item.last_seen_at.isoformat() if item.last_seen_at else None,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in rows
        ],
    }
