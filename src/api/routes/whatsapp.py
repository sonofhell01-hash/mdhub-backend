from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.core.db_session import get_db
from src.models.core import Collaborator, Document, WhatsAppContact, WhatsAppHistory, WhatsAppQueue
from src.services.whatsapp.contacts import find_contact, import_contacts_csv, upsert_contact
from src.services.whatsapp.message_builder import DOCUMENT_LABELS, MessageBuilder
from src.services.whatsapp.phone_utils import format_brazil_whatsapp
from src.core.config import settings
from src.services.whatsapp.queue import (
    mark_queue_sent,
    preview_document_reminder,
    queue_document_reminder,
    queue_pending_document_reminders,
    send_pending_batch,
    send_queue_item,
)
from src.services.whatsapp.rules import BLOCKED_ROLE_KEYWORDS, blocked_role_reason
from src.services.midiasimples.session_store import get_session


router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])

ALLOWED_RAT_TECHNICIANS = {
    "MARCEL DIEGO SILVA",
    "MARCOS PAULO DA SILVA REIS",
    "CAIO VINICIUS PEREIRA DA SILVA FREITAS",
    "MICHEL PURCINA DELOCCO",
}

SIGNED_STATUS_KEYS = {
    "ASSINADO",
    "ASSINADA",
    "ASSINADA POR COMPLETA",
    "ASSINADO POR COMPLETA",
    "COMPLETED",
    "CONCLUIDO",
    "CONCLUIDA",
    "FINALIZADO",
    "FINALIZADA",
}


class ContactUpsertRequest(BaseModel):
    matricula: str | None = None
    nome: str | None = None
    telefone: str
    email: str | None = None
    cargo: str | None = None
    fonte: str = "manual"
    payload: dict[str, Any] = Field(default_factory=dict)


class ImportContactsRequest(BaseModel):
    path: str
    fonte: str = "sap_report"


class MessagePreviewRequest(BaseModel):
    template: Literal["lembrete_assinatura", "reforco", "assinado", "devolucao", "personalizada"] = "lembrete_assinatura"
    nome: str
    tipo_documento: str = "rat"
    numero_documento: str = "manual"
    dias_pendente: int = 3
    equipamento: str | None = None
    texto: str | None = None


class QueueDocumentRequest(BaseModel):
    document_id: int
    usuario_id: int | None = None
    telefone: str | None = None
    force: bool = False


class DirectQueueRequest(BaseModel):
    telefone: str
    mensagem: str
    tipo_mensagem: str = "lembrete_assinatura"
    documento_id: int | None = None
    colaborador_id: int | None = None
    usuario_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class MarkSentRequest(BaseModel):
    response_payload: dict[str, Any] = Field(default_factory=dict)


class SendBatchRequest(BaseModel):
    limit: int = Field(default=1, ge=1, le=10)


class QueueRemindersRequest(BaseModel):
    usuario_id: int | None = None
    limit: int = Field(default=20, ge=1, le=100)
    include_drafts: bool = False
    force: bool = False


class RatReminderSyncRequest(BaseModel):
    email: str
    max_pages: int = Field(default=30, ge=1, le=200)
    page_size: int = Field(default=100, ge=10, le=100)
    queue_limit: int = Field(default=200, ge=1, le=1000)
    force: bool = False


class ResetQueueRequest(BaseModel):
    clear_history: bool = True
    clear_documents: bool = False
    only_whatsapp: bool = True


def _normalize_name(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _rat_responsible(row: dict[str, Any]) -> str:
    return str(
        _pick(
            row,
            "arklok_responsible",
            "responsible",
            "responsavel_arklok",
            "responsavel",
            "analyst",
            "analista",
            "technician",
            "tecnico",
        )
        or ""
    ).strip()


def _rat_signature_status(row: dict[str, Any]) -> str:
    return str(
        _pick(
            row,
            "docusign_status",
            "status_docusign",
            "document_status",
            "signature_status",
            "status_assinatura",
            "status",
        )
        or ""
    ).strip()


def _rat_is_signed(row: dict[str, Any]) -> bool:
    status = _normalize_name(_rat_signature_status(row))
    if not status:
        return False
    return status in SIGNED_STATUS_KEYS or "COMPLET" in status or "ASSINAD" in status


def _allowed_rat_technician(row: dict[str, Any]) -> bool:
    responsible = _normalize_name(_rat_responsible(row))
    if not responsible:
        return False
    return responsible in {_normalize_name(name) for name in ALLOWED_RAT_TECHNICIANS}


def _extract_called_number(row: dict[str, Any]) -> str | None:
    value = _pick(row, "ticket", "numero_chamado", "call_number", "incident", "chamado")
    if value:
        return str(value).strip().upper()
    action = str(row.get("action") or "")
    match = re.search(r"(INC|REQ|LNR)\d{5,}", action, re.IGNORECASE)
    return match.group(0).upper() if match else None


def _upsert_rat_document_from_row(db: Session, row: dict[str, Any]) -> Document:
    matricula = str(_pick(row, "registration", "matricula", "registro") or "").strip().lstrip("F")
    nome = str(_pick(row, "name", "client_name", "nome", "colaborador") or matricula or "Colaborador").strip()
    email = str(_pick(row, "email", "client_email") or "").strip() or None
    cargo = str(_pick(row, "profile", "cargo") or "").strip() or None
    serial = str(_pick(row, "serial", "product_serial_number") or "").strip() or None
    external_id = str(_pick(row, "id") or "").strip()

    collaborator = None
    if matricula:
        collaborator = db.query(Collaborator).filter(Collaborator.matricula == matricula).first()
        if collaborator:
            collaborator.nome = nome or collaborator.nome
            collaborator.email = email or collaborator.email
            collaborator.cargo = cargo or collaborator.cargo
            collaborator.fonte = "midiasimples_rat"
        else:
            collaborator = Collaborator(
                matricula=matricula,
                nome=nome,
                email=email,
                cargo=cargo,
                fonte="midiasimples_rat",
            )
            db.add(collaborator)
            db.flush()

    document = (
        db.query(Document)
        .filter(Document.tipo == "rat", Document.midiasimples_id == external_id)
        .first()
        if external_id
        else None
    )
    payload = {
        "colaborador": {
            "matricula": matricula or None,
            "nome": nome,
            "email": email,
            "cargo": cargo,
        },
        "dados": {
            "tecnico": {"nome": _rat_responsible(row)},
            "midiasimples": row,
            "rat": {
                "signature_status": _rat_signature_status(row),
                "responsible": _rat_responsible(row),
            },
            "equipamento_atual": {"serial": serial},
        },
    }
    if document:
        document.colaborador_id = collaborator.id if collaborator else document.colaborador_id
        document.numero_chamado = _extract_called_number(row) or document.numero_chamado
        document.status = "pronto_envio"
        document.payload = payload
        return document

    document = Document(
        tipo="rat",
        colaborador_id=collaborator.id if collaborator else None,
        numero_chamado=_extract_called_number(row),
        midiasimples_id=external_id or None,
        status="pronto_envio",
        payload=payload,
        sync_pendente=False,
    )
    db.add(document)
    db.flush()
    return document


def _contact_dict(contact: WhatsAppContact | None) -> dict[str, Any] | None:
    if not contact:
        return None
    return {
        "id": contact.id,
        "colaborador_id": contact.colaborador_id,
        "matricula": contact.matricula,
        "nome": contact.nome,
        "email": contact.email,
        "cargo": contact.cargo,
        "telefone": contact.telefone,
        "telefone_formatado": contact.telefone_formatado,
        "fonte": contact.fonte,
        "status": contact.status,
        "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
        "created_at": contact.created_at.isoformat() if contact.created_at else None,
    }


def _queue_dict(item: WhatsAppQueue) -> dict[str, Any]:
    return {
        "id": item.id,
        "documento_id": item.documento_id,
        "colaborador_id": item.colaborador_id,
        "usuario_id": item.usuario_id,
        "telefone": item.telefone,
        "tipo_mensagem": item.tipo_mensagem,
        "status": item.status,
        "tentativas": item.tentativas,
        "ultimo_erro": item.ultimo_erro,
        "motivo_bloqueio": item.motivo_bloqueio,
        "agendado_para": item.agendado_para.isoformat() if item.agendado_para else None,
        "enviado_em": item.enviado_em.isoformat() if item.enviado_em else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "mensagem": item.mensagem,
        "payload": item.payload,
    }


def _history_dict(item: WhatsAppHistory) -> dict[str, Any]:
    return {
        "id": item.id,
        "queue_id": item.queue_id,
        "documento_id": item.documento_id,
        "colaborador_id": item.colaborador_id,
        "usuario_id": item.usuario_id,
        "telefone": item.telefone,
        "status": item.status,
        "tipo_mensagem": item.tipo_mensagem,
        "motivo_bloqueio": item.motivo_bloqueio,
        "erro": item.erro,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "mensagem": item.mensagem,
        "payload": item.payload,
    }


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return {
        "status": "ready",
        "worker": {
            "mode": "manual_controlled",
            "chrome_debug_port": settings.whatsapp_chrome_debug_port,
            "batch_size": settings.whatsapp_batch_size,
            "max_attempts": settings.whatsapp_max_attempts,
            "send_wait_seconds": settings.whatsapp_send_wait_seconds,
            "between_messages_seconds": settings.whatsapp_between_messages_seconds,
        },
        "contacts": db.query(WhatsAppContact).count(),
        "queue": {
            "pendente": db.query(WhatsAppQueue).filter(WhatsAppQueue.status == "pendente").count(),
            "bloqueado": db.query(WhatsAppQueue).filter(WhatsAppQueue.status == "bloqueado").count(),
            "enviado": db.query(WhatsAppQueue).filter(WhatsAppQueue.status == "enviado").count(),
            "falha": db.query(WhatsAppQueue).filter(WhatsAppQueue.status == "falha").count(),
        },
        "blocked_roles": list(BLOCKED_ROLE_KEYWORDS),
    }


@router.get("/contacts/search")
def search_contact(q: str = Query(..., min_length=2), db: Session = Depends(get_db)):
    contact = find_contact(db, q)
    if not contact:
        raise HTTPException(status_code=404, detail="Contato nao encontrado.")
    return {"status": "ok", "contact": _contact_dict(contact)}


@router.post("/contacts")
def upsert_contact_endpoint(body: ContactUpsertRequest, db: Session = Depends(get_db)):
    contact = upsert_contact(
        db,
        matricula=body.matricula,
        nome=body.nome,
        telefone=body.telefone,
        email=body.email,
        cargo=body.cargo,
        fonte=body.fonte,
        payload=body.payload,
    )
    if not contact:
        raise HTTPException(status_code=400, detail="Telefone invalido.")
    db.commit()
    return {"status": "ok", "contact": _contact_dict(contact)}


@router.post("/contacts/import-csv")
def import_contacts(body: ImportContactsRequest, db: Session = Depends(get_db)):
    path = Path(body.path)
    try:
        result = import_contacts_csv(db, path, fonte=body.fonte)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Arquivo nao encontrado.")
    db.commit()
    return {"status": "ok", **result}


@router.get("/messages/templates")
def templates():
    return {
        "templates": ["lembrete_assinatura", "reforco", "assinado", "devolucao", "personalizada"],
        "document_labels": DOCUMENT_LABELS,
    }


@router.post("/messages/preview")
def preview_message(body: MessagePreviewRequest):
    builder = MessageBuilder()
    if body.template == "lembrete_assinatura":
        message = builder.signature_reminder(
            nome=body.nome,
            tipo_documento=body.tipo_documento,
            numero_documento=body.numero_documento,
        )
    elif body.template == "reforco":
        message = builder.reminder_followup(
            nome=body.nome,
            tipo_documento=body.tipo_documento,
            numero_documento=body.numero_documento,
            dias_pendente=body.dias_pendente,
        )
    elif body.template == "assinado":
        message = builder.signed_success(
            nome=body.nome,
            tipo_documento=body.tipo_documento,
            numero_documento=body.numero_documento,
        )
    elif body.template == "devolucao":
        message = builder.return_notice(nome=body.nome, equipamento=body.equipamento or "Equipamento pendente")
    else:
        message = builder.custom(nome=body.nome, texto=body.texto or "")
    return {"status": "ok", "message": message}


@router.post("/messages/preview-document")
def preview_document(body: QueueDocumentRequest, db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "preview": preview_document_reminder(db, document_id=body.document_id, telefone=body.telefone)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/queue")
def queue_message(body: QueueDocumentRequest, db: Session = Depends(get_db)):
    try:
        item = queue_document_reminder(
            db,
            document_id=body.document_id,
            usuario_id=body.usuario_id,
            telefone=body.telefone,
            force=body.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"status": "ok", "item": _queue_dict(item)}


@router.post("/queue/direct")
def queue_direct_message(body: DirectQueueRequest, db: Session = Depends(get_db)):
    phone = format_brazil_whatsapp(body.telefone)
    if not phone:
        raise HTTPException(status_code=400, detail="Telefone invalido para WhatsApp.")
    message = str(body.mensagem or "").strip()
    colaborador_id = body.colaborador_id
    preview_payload: dict[str, Any] | None = None
    if body.tipo_mensagem == "lembrete_assinatura" and body.documento_id:
        try:
            preview = preview_document_reminder(db, document_id=body.documento_id, telefone=phone)
            message = preview["mensagem"]
            preview_payload = preview
            colaborador_id = preview.get("colaborador_id") or colaborador_id
        except ValueError:
            # Mantem compatibilidade com clients que encaminham lembretes de documentos
            # que ainda nao existem no HUB central.
            pass
    if not message:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")

    existing_query = (
        db.query(WhatsAppQueue)
        .filter(
            WhatsAppQueue.telefone == phone,
            WhatsAppQueue.tipo_mensagem == body.tipo_mensagem,
            WhatsAppQueue.status.in_(("pendente", "bloqueado")),
        )
        .order_by(WhatsAppQueue.created_at.desc())
    )
    if colaborador_id:
        existing_query = existing_query.filter(WhatsAppQueue.colaborador_id == colaborador_id)
    elif body.documento_id:
        existing_query = existing_query.filter(WhatsAppQueue.documento_id == body.documento_id)
    existing = existing_query.first()
    if existing:
        existing.documento_id = body.documento_id or existing.documento_id
        existing.colaborador_id = colaborador_id or existing.colaborador_id
        existing.usuario_id = body.usuario_id or existing.usuario_id
        existing.mensagem = message
        existing.payload = {
            **(existing.payload or {}),
            **(body.payload or {}),
            "origin": "central_direct_queue",
            "deduplicated": True,
            "preview": preview_payload,
        }
        db.add(
            WhatsAppHistory(
                queue_id=existing.id,
                documento_id=existing.documento_id,
                colaborador_id=existing.colaborador_id,
                usuario_id=existing.usuario_id,
                telefone=existing.telefone,
                mensagem=existing.mensagem,
                status=existing.status,
                tipo_mensagem=existing.tipo_mensagem,
                payload={"origin": "central_direct_queue", "deduplicated": True},
            )
        )
        db.commit()
        return {"status": "ok", "item": _queue_dict(existing), "deduplicated": True}

    item = WhatsAppQueue(
        documento_id=body.documento_id,
        colaborador_id=colaborador_id,
        usuario_id=body.usuario_id,
        telefone=phone,
        mensagem=message,
        tipo_mensagem=body.tipo_mensagem,
        status="pendente",
        payload={**(body.payload or {}), "origin": "central_direct_queue", "preview": preview_payload},
    )
    db.add(item)
    db.flush()
    db.add(
        WhatsAppHistory(
            queue_id=item.id,
            documento_id=item.documento_id,
            colaborador_id=item.colaborador_id,
            usuario_id=item.usuario_id,
            telefone=item.telefone,
            mensagem=item.mensagem,
            status=item.status,
            tipo_mensagem=item.tipo_mensagem,
            payload={"origin": "central_direct_queue"},
        )
    )
    db.commit()
    return {"status": "ok", "item": _queue_dict(item)}


@router.post("/queue/reminders")
def queue_reminders(body: QueueRemindersRequest, db: Session = Depends(get_db)):
    result = queue_pending_document_reminders(
        db,
        usuario_id=body.usuario_id,
        limit=body.limit,
        include_drafts=body.include_drafts,
        force=body.force,
    )
    db.commit()
    return {"status": "ok", **result}


@router.post("/queue/sync-rat-reminders")
def sync_rat_reminders(body: RatReminderSyncRequest, db: Session = Depends(get_db)):
    stored = get_session(body.email)
    if not stored:
        raise HTTPException(status_code=401, detail="Sessao MidiaSimples nao esta ativa para este tecnico.")

    scanned = 0
    eligible = 0
    signed = 0
    out_of_scope = 0
    queued = 0
    skipped: list[dict[str, Any]] = []
    queued_items: list[dict[str, Any]] = []

    for page in range(body.max_pages):
        start = page * body.page_size
        payload = stored.session.datatable("/rat-attendance", search="", start=start, length=body.page_size)
        rows = payload.get("data") or []
        if not rows:
            break

        for row in rows:
            scanned += 1
            if not _allowed_rat_technician(row):
                out_of_scope += 1
                continue
            if _rat_is_signed(row):
                signed += 1
                continue

            eligible += 1
            document = _upsert_rat_document_from_row(db, row)
            existing = (
                db.query(WhatsAppQueue)
                .filter(
                    WhatsAppQueue.documento_id == document.id,
                    WhatsAppQueue.tipo_mensagem == "lembrete_assinatura",
                    WhatsAppQueue.status.in_(("pendente", "bloqueado", "enviado")),
                )
                .first()
            )
            if existing:
                skipped.append(
                    {
                        "document_id": document.id,
                        "queue_id": existing.id,
                        "reason": f"ja existe lembrete {existing.status}",
                    }
                )
                continue

            if queued >= body.queue_limit:
                skipped.append({"document_id": document.id, "reason": "limite de fila atingido"})
                continue

            try:
                item = queue_document_reminder(db, document_id=document.id, force=body.force)
            except ValueError as exc:
                skipped.append({"document_id": document.id, "reason": str(exc)})
                continue

            queued += 1
            queued_items.append(
                {
                    "document_id": document.id,
                    "queue_id": item.id,
                    "status": item.status,
                    "telefone": item.telefone,
                    "rat_id": document.midiasimples_id,
                    "numero_chamado": document.numero_chamado,
                }
            )

    db.commit()
    return {
        "status": "ok",
        "scanned": scanned,
        "eligible": eligible,
        "signed": signed,
        "out_of_scope": out_of_scope,
        "queued": queued,
        "skipped": len(skipped),
        "allowed_technicians": sorted(ALLOWED_RAT_TECHNICIANS),
        "items": queued_items,
        "skipped_items": skipped[:100],
    }


@router.post("/reset-operational")
def reset_operational_lists(body: ResetQueueRequest, db: Session = Depends(get_db)):
    deleted_history = 0
    deleted_queue = db.query(WhatsAppQueue).delete(synchronize_session=False)
    if body.clear_history:
        deleted_history = db.query(WhatsAppHistory).delete(synchronize_session=False)

    deleted_documents = 0
    if body.clear_documents:
        document_query = db.query(Document)
        if body.only_whatsapp:
            document_query = document_query.filter(Document.tipo == "rat")
        deleted_documents = document_query.delete(synchronize_session=False)

    db.commit()
    return {
        "status": "ok",
        "deleted_queue": deleted_queue,
        "deleted_history": deleted_history,
        "deleted_documents": deleted_documents,
    }


@router.get("/queue")
def queue_list(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(WhatsAppQueue)
    if status:
        query = query.filter(WhatsAppQueue.status == status)
    items = query.order_by(WhatsAppQueue.created_at.desc()).limit(limit).all()
    return {"items": [_queue_dict(item) for item in items]}


@router.post("/queue/{queue_id}/mark-sent")
def mark_sent(queue_id: int, body: MarkSentRequest, db: Session = Depends(get_db)):
    try:
        item = mark_queue_sent(db, queue_id=queue_id, response_payload=body.response_payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    db.commit()
    return {"status": "ok", "item": _queue_dict(item)}


@router.post("/queue/{queue_id}/send")
def send_one(queue_id: int, db: Session = Depends(get_db)):
    try:
        item = send_queue_item(db, queue_id=queue_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"status": item.status, "item": _queue_dict(item)}


@router.post("/queue/send-pending")
def send_pending(body: SendBatchRequest, db: Session = Depends(get_db)):
    limit = min(body.limit, settings.whatsapp_batch_size)
    result = send_pending_batch(db, limit=limit)
    db.commit()
    return {"status": "ok", **result}


@router.get("/history")
def history(
    limit: int = Query(50, ge=1, le=200),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(WhatsAppHistory)
    if status:
        query = query.filter(WhatsAppHistory.status == status)
    items = query.order_by(WhatsAppHistory.created_at.desc()).limit(limit).all()
    return {"items": [_history_dict(item) for item in items]}


@router.get("/phone/format")
def phone_format(value: str = Query(...)):
    formatted = format_brazil_whatsapp(value)
    return {"input": value, "formatted": formatted, "valid": bool(formatted)}


@router.get("/roles/check")
def role_check(cargo: str = Query(...)):
    reason = blocked_role_reason(cargo)
    return {"cargo": cargo, "blocked": bool(reason), "reason": reason}
