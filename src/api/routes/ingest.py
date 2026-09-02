from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db_session import get_db
from src.models.core import AuditLog, Collaborator, Document


router = APIRouter(prefix="/api/ingest", tags=["Ingestao Central"])


EVENT_TO_DOCUMENT_TYPE = {
    "RAT_CREATED": "rat",
    "LAUDO_CREATED": "laudo",
    "DEVOLUCAO_CREATED": "devolucao",
    "CONCESSAO_CREATED": "concessao",
    "SUBSTITUICAO_CREATED": "substituicao",
    "SUBSTITUICAO_HEADSET_CREATED": "substituicao_headset",
    "EMPRESTIMO_CREATED": "emprestimo",
    "ROLLOUT_CREATED": "rollout",
    "FECHAMENTO_CREATED": "fechamento",
    "MANUAL_EVENT": "manual",
}


class IngestEventRequest(BaseModel):
    event_id: str | int | None = None
    type: str = Field(..., min_length=1)
    created_at: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def _auth_or_raise(authorization: str | None) -> None:
    expected = settings.sync_ingest_token
    if not expected:
        raise HTTPException(status_code=503, detail="SYNC_INGEST_TOKEN nao configurado no servidor.")
    if not authorization:
        raise HTTPException(status_code=401, detail="Token ausente.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != expected:
        raise HTTPException(status_code=403, detail="Token invalido.")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pick(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _extract_inner_payload(payload: dict[str, Any]) -> dict[str, Any]:
    inner = payload.get("payload")
    return inner if isinstance(inner, dict) else {}


def _extract_collaborator(payload: dict[str, Any]) -> dict[str, Any]:
    inner = _extract_inner_payload(payload)
    dados = _as_dict(inner.get("dados"))
    return (
        _as_dict(payload.get("colaborador"))
        or _as_dict(inner.get("colaborador"))
        or _as_dict(dados.get("colaborador"))
    )


def _extract_call_number(payload: dict[str, Any]) -> str | None:
    inner = _extract_inner_payload(payload)
    dados = _as_dict(inner.get("dados"))
    return _pick(
        payload.get("numero_chamado"),
        inner.get("numero_chamado"),
        dados.get("numero_chamado"),
        payload.get("ticket"),
        inner.get("ticket"),
    )


def _extract_source_document_id(payload: dict[str, Any]) -> str | None:
    inner = _extract_inner_payload(payload)
    value = _pick(payload.get("document_id"), inner.get("document_id"), payload.get("id"))
    return str(value) if value not in (None, "") else None


def _upsert_collaborator(db: Session, data: dict[str, Any]) -> Collaborator | None:
    matricula = str(_pick(data.get("matricula"), data.get("registration"), data.get("registro")) or "").strip()
    if not matricula:
        return None

    collaborator = db.query(Collaborator).filter(Collaborator.matricula == matricula).first()
    if not collaborator:
        collaborator = Collaborator(
            matricula=matricula,
            nome=str(_pick(data.get("nome"), data.get("name"), matricula)),
            fonte="ingest_desktop",
        )
        db.add(collaborator)
        db.flush()

    collaborator.nome = str(_pick(data.get("nome"), data.get("name"), collaborator.nome))
    collaborator.email = _pick(data.get("email"), collaborator.email)
    collaborator.telefone = _pick(data.get("telefone"), data.get("phone"), collaborator.telefone)
    collaborator.cargo = _pick(data.get("cargo"), data.get("role"), collaborator.cargo)
    collaborator.regional = _pick(data.get("regional"), data.get("subsidiary"), collaborator.regional)
    collaborator.fonte = "ingest_desktop"
    return collaborator


@router.post("/events")
def ingest_event(
    body: IngestEventRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    _auth_or_raise(authorization)

    event_type = body.type.strip().upper()
    document_type = EVENT_TO_DOCUMENT_TYPE.get(event_type)
    if not document_type:
        raise HTTPException(status_code=400, detail=f"Tipo de evento invalido: {event_type}")

    payload = body.payload or {}
    collaborator = _upsert_collaborator(db, _extract_collaborator(payload))
    numero_chamado = _extract_call_number(payload)
    source_document_id = _extract_source_document_id(payload)
    source_machine = _pick(payload.get("maquina_origem"), payload.get("source_machine"), payload.get("machine"))

    document = None
    if document_type != "manual":
        query = db.query(Document).filter(
            Document.tipo == document_type,
            Document.midiasimples_id == source_document_id,
        )
        document = query.first() if source_document_id else None
        if not document:
            document = Document(
                tipo=document_type,
                midiasimples_id=source_document_id,
                status="recebido",
                sync_pendente=False,
            )
            db.add(document)
            db.flush()

        document.colaborador_id = collaborator.id if collaborator else document.colaborador_id
        document.numero_chamado = numero_chamado or document.numero_chamado
        document.status = "recebido"
        document.payload = {
            "central_ingest": {
                "event_type": event_type,
                "source_event_id": str(body.event_id) if body.event_id is not None else None,
                "source_document_id": source_document_id,
                "source_machine": source_machine,
                "payload": payload,
                "received_at": datetime.now(timezone.utc).isoformat(),
            }
        }

    db.add(
        AuditLog(
            acao="CENTRAL_INGEST_RECEIVED",
            modulo="ingest",
            resultado="ok",
            payload={
                "event_type": event_type,
                "event_id": body.event_id,
                "document_type": document_type,
                "document_id": document.id if document else None,
                "numero_chamado": numero_chamado,
            },
        )
    )
    db.commit()

    return {
        "status": "ok",
        "central_event_id": document.id if document else None,
        "document_id": document.id if document else None,
        "collaborator_id": collaborator.id if collaborator else None,
        "message": "Evento recebido pelo HUB Central.",
    }
