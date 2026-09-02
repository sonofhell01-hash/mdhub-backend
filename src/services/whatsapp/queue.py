from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
import unicodedata

import requests
from sqlalchemy.orm import Session

from src.core.config import settings
from src.models.core import AuditLog, Collaborator, Document, WhatsAppContact, WhatsAppHistory, WhatsAppQueue
from src.services.whatsapp.message_builder import MessageBuilder
from src.services.whatsapp.phone_utils import format_brazil_whatsapp
from src.services.whatsapp.rules import blocked_role_reason
from src.services.whatsapp.web_sender import WhatsAppSendError, WhatsAppWebSender, _interprocess_send_lock


ACTIVE_QUEUE_STATUSES = ("pendente", "bloqueado", "enviado")
SIGNABLE_DOCUMENT_TYPES = {"rat", "laudo", "devolucao", "substituicao", "substituicao_headset", "concessao", "emprestimo", "rollout"}
PENDING_SIGNATURE_STATUSES = {"pronto_envio", "enviado", "midiasimples_enviado"}
SIGNED_STATUS_MARKERS = ("ASSINAD", "COMPLET", "CONCLUID", "FINALIZAD")
TRUE_VALUES = {"1", "SIM", "S", "TRUE", "YES", "Y"}

# Inicio formal da operacao do bot. Lembretes anteriores a esta data nao
# podem sair, mesmo que tenham permanecido pendentes na fila.
WHATSAPP_OPERATION_START_DATE = date(2026, 7, 16)
BUSINESS_HOURS_START = time(8, 0)
BUSINESS_HOURS_END = time(18, 0)


def is_whatsapp_business_hours(now: datetime | None = None) -> bool:
    current = now or datetime.now()
    return current.weekday() < 5 and BUSINESS_HOURS_START <= current.time() < BUSINESS_HOURS_END


def _block_pre_operation_reminder(db: Session, item: WhatsAppQueue) -> bool:
    if item.tipo_mensagem != "lembrete_assinatura" or not item.documento_id:
        return False
    document = db.get(Document, item.documento_id)
    created_at = document.created_at if document else None
    if created_at and created_at.date() >= WHATSAPP_OPERATION_START_DATE:
        return False

    reason = (
        "Documento anterior ao inicio formal do bot em "
        f"{WHATSAPP_OPERATION_START_DATE.strftime('%d/%m/%Y')} nao recebe lembrete."
    )
    item.status = "bloqueado"
    item.motivo_bloqueio = reason
    item.ultimo_erro = reason
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
            motivo_bloqueio=reason,
            payload={"origin": "whatsapp_operation_start_guard"},
        )
    )
    return True


def resolve_document(db: Session, document_id: int) -> Document | None:
    """Accept either the local document ID or the real MidiaSimples ID."""
    document = db.get(Document, document_id)
    if document:
        return document
    return (
        db.query(Document)
        .filter(Document.midiasimples_id == str(document_id))
        .order_by(Document.created_at.desc())
        .first()
    )


def _document_number(document: Document | None) -> str:
    if not document:
        return "manual"
    numero = str(document.numero_chamado or "").strip()
    if numero and numero.upper() not in {"INC0000000", "MANUAL", "NA", "N/A"}:
        return numero
    return document.midiasimples_id or numero or str(document.id)


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _walk_values(data: Any):
    if isinstance(data, dict):
        for key, value in data.items():
            key_text = str(key).lower()
            is_signature_status = any(token in key_text for token in ("signature", "assinatura", "docusign", "status"))
            is_individual_recipient = any(
                token in key_text
                for token in (
                    "technician",
                    "technical",
                    "tecnico",
                    "customer",
                    "client",
                    "usuario",
                    "colaborador",
                    "responsible_signature",
                    "arklok_signature",
                )
            )
            if is_signature_status and not is_individual_recipient:
                yield value
            yield from _walk_values(value)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_values(item)


def _document_is_signed(document: Document) -> bool:
    values = [document.status]
    values.extend(_walk_values(document.payload or {}))
    values.extend(_walk_values(document.response_payload or {}))
    for value in values:
        normalized = _normalize_text(value)
        if normalized and any(marker in normalized for marker in SIGNED_STATUS_MARKERS):
            return True
    return False


def _dict_path(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return _normalize_text(value) in TRUE_VALUES


def _document_requires_customer_signature(document: Document) -> bool:
    if document.tipo != "laudo":
        return True

    payload = document.payload or {}
    response = document.response_payload or {}
    markers = [
        _dict_path(payload, "dados", "laudo", "uso_inadequado"),
        _dict_path(payload, "laudo", "uso_inadequado"),
        _dict_path(response, "row", "case3"),
        _dict_path(response, "row", "case5"),
    ]
    return any(_is_true(value) for value in markers)


def _pending_signature_documents(db: Session, document: Document) -> list[Document]:
    if not document.colaborador_id:
        return [document] if _document_requires_customer_signature(document) and not _document_is_signed(document) else []
    documents = (
        db.query(Document)
        .filter(Document.colaborador_id == document.colaborador_id)
        .filter(Document.status.in_(PENDING_SIGNATURE_STATUSES))
        .filter(Document.tipo.in_(SIGNABLE_DOCUMENT_TYPES))
        .order_by(Document.created_at.asc(), Document.id.asc())
        .all()
    )
    documents = [item for item in documents if _document_requires_customer_signature(item) and not _document_is_signed(item)]
    if _document_requires_customer_signature(document) and not _document_is_signed(document) and not any(item.id == document.id for item in documents):
        documents.append(document)
    return documents


def _document_entries(documents: list[Document]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": item.id,
            "tipo_documento": item.tipo,
            "numero_documento": _document_number(item),
        }
        for item in documents
    ]


def preview_document_reminder(
    db: Session,
    *,
    document_id: int,
    telefone: str | None = None,
    aggregate_pending: bool = True,
) -> dict[str, Any]:
    document = resolve_document(db, document_id)
    if not document:
        raise ValueError("Documento nao encontrado.")
    payload = document.payload or {}
    dados = payload.get("dados") if isinstance(payload.get("dados"), dict) else {}
    if payload.get("_test_data") or dados.get("_test_data"):
        raise ValueError("Dado ficticio de teste: WhatsApp bloqueado.")
    if document.tipo not in SIGNABLE_DOCUMENT_TYPES:
        raise ValueError("Este item nao e documento de assinatura. Fechamento de RAT deve ser copiado e colado no chamado.")
    collaborator = db.get(Collaborator, document.colaborador_id) if document.colaborador_id else None
    contact = None
    if collaborator:
        contact = (
            db.query(WhatsAppContact)
            .filter(WhatsAppContact.matricula == collaborator.matricula, WhatsAppContact.status == "ativo")
            .order_by(WhatsAppContact.updated_at.desc())
            .first()
        )

    phone = format_brazil_whatsapp(telefone or (contact.telefone_formatado if contact else None) or (collaborator.telefone if collaborator else None))
    if not phone:
        raise ValueError("Telefone nao encontrado ou invalido.")

    role = collaborator.cargo if collaborator else contact.cargo if contact else None
    blocked_reason = blocked_role_reason(role)
    name = collaborator.nome if collaborator else contact.nome if contact else "Colaborador"
    pending_documents = (
        _pending_signature_documents(db, document)
        if aggregate_pending
        else ([document] if _document_requires_customer_signature(document) and not _document_is_signed(document) else [])
    )
    if not pending_documents:
        raise ValueError("Nenhum documento deste colaborador exige lembrete de assinatura no momento.")
    document_entries = _document_entries(pending_documents)
    message = MessageBuilder().signature_reminder_many(nome=name, documentos=document_entries)
    return {
        "document_id": document.id,
        "colaborador_id": collaborator.id if collaborator else None,
        "nome": name,
        "telefone": phone,
        "tipo_mensagem": "lembrete_assinatura",
        "bloqueado": bool(blocked_reason),
        "motivo_bloqueio": blocked_reason,
        "mensagem": message,
        "documentos_pendentes": document_entries,
    }


def queue_document_reminder(
    db: Session,
    *,
    document_id: int,
    usuario_id: int | None = None,
    telefone: str | None = None,
    force: bool = False,
    aggregate_pending: bool = True,
) -> WhatsAppQueue:
    document = resolve_document(db, document_id)
    if not document:
        raise ValueError("Documento nao encontrado.")
    preview = preview_document_reminder(
        db,
        document_id=document.id,
        telefone=telefone,
        aggregate_pending=aggregate_pending,
    )
    status = "bloqueado" if preview["bloqueado"] and not force else "pendente"
    item = WhatsAppQueue(
        documento_id=document.id,
        colaborador_id=preview["colaborador_id"],
        usuario_id=usuario_id,
        telefone=preview["telefone"],
        mensagem=preview["mensagem"],
        tipo_mensagem=preview["tipo_mensagem"],
        status=status,
        motivo_bloqueio=preview["motivo_bloqueio"] if status == "bloqueado" else None,
        payload={"preview": preview, "force": force, "aggregate_pending": aggregate_pending},
    )
    db.add(item)
    db.flush()
    db.add(
        WhatsAppHistory(
            queue_id=item.id,
            documento_id=item.documento_id,
            colaborador_id=item.colaborador_id,
            usuario_id=usuario_id,
            telefone=item.telefone,
            mensagem=item.mensagem,
            status=item.status,
            tipo_mensagem=item.tipo_mensagem,
            motivo_bloqueio=item.motivo_bloqueio,
            payload={"origin": "queue_document_reminder"},
        )
    )
    db.add(
        AuditLog(
            usuario_id=usuario_id,
            acao="WHATSAPP_MESSAGE_QUEUED",
            modulo="whatsapp",
            resultado=item.status,
            payload={"queue_id": item.id, "document_id": document.id, "input_document_id": document_id, "telefone": item.telefone},
        )
    )
    return item


def queue_pending_document_reminders(
    db: Session,
    *,
    usuario_id: int | None = None,
    limit: int = 20,
    include_drafts: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    statuses = ["pronto_envio"]

    documents = (
        db.query(Document)
        .filter(Document.status.in_(statuses))
        .filter(Document.tipo.in_(SIGNABLE_DOCUMENT_TYPES))
        .filter(Document.enviado_em.is_(None))
        .order_by(Document.created_at.asc())
        .limit(limit)
        .all()
    )

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for document in documents:
        existing = (
            db.query(WhatsAppQueue)
            .filter(
                WhatsAppQueue.documento_id == document.id,
                WhatsAppQueue.tipo_mensagem == "lembrete_assinatura",
                WhatsAppQueue.status.in_(ACTIVE_QUEUE_STATUSES),
            )
            .order_by(WhatsAppQueue.created_at.desc())
            .first()
        )
        if existing:
            skipped.append(
                {
                    "document_id": document.id,
                    "reason": f"ja existe mensagem {existing.status} na fila",
                    "queue_id": existing.id,
                }
            )
            continue

        try:
            item = queue_document_reminder(
                db,
                document_id=document.id,
                usuario_id=usuario_id,
                force=force,
            )
        except ValueError as exc:
            skipped.append({"document_id": document.id, "reason": str(exc)})
            continue

        entry = {
            "document_id": document.id,
            "queue_id": item.id,
            "status": item.status,
            "telefone": item.telefone,
            "tipo": document.tipo,
            "numero_chamado": document.numero_chamado,
        }
        if item.status == "bloqueado":
            blocked.append({**entry, "reason": item.motivo_bloqueio})
        else:
            created.append(entry)

    return {
        "processed": len(documents),
        "created": len(created),
        "blocked": len(blocked),
        "skipped": len(skipped),
        "items": created,
        "blocked_items": blocked,
        "skipped_items": skipped,
    }


def mark_queue_sent(db: Session, *, queue_id: int, response_payload: dict[str, Any] | None = None) -> WhatsAppQueue:
    item = db.get(WhatsAppQueue, queue_id)
    if not item:
        raise ValueError("Item de fila nao encontrado.")
    item.status = "enviado"
    item.enviado_em = datetime.now()
    item.payload = {**(item.payload or {}), "response": response_payload or {}}
    db.add(
        WhatsAppHistory(
            queue_id=item.id,
            documento_id=item.documento_id,
            colaborador_id=item.colaborador_id,
            usuario_id=item.usuario_id,
            telefone=item.telefone,
            mensagem=item.mensagem,
            status="enviado",
            tipo_mensagem=item.tipo_mensagem,
            payload=response_payload,
        )
    )
    return item


def mark_queue_failed(db: Session, *, item: WhatsAppQueue, error: str) -> WhatsAppQueue:
    item.status = "falha"
    item.tentativas += 1
    item.ultimo_erro = error
    db.add(
        WhatsAppHistory(
            queue_id=item.id,
            documento_id=item.documento_id,
            colaborador_id=item.colaborador_id,
            usuario_id=item.usuario_id,
            telefone=item.telefone,
            mensagem=item.mensagem,
            status="falha",
            tipo_mensagem=item.tipo_mensagem,
            erro=error,
        )
    )
    return item


def send_queue_item(db: Session, *, queue_id: int, sender: WhatsAppWebSender | None = None) -> WhatsAppQueue:
    item = db.get(WhatsAppQueue, queue_id)
    if not item:
        raise ValueError("Item de fila nao encontrado.")
    if item.status != "pendente":
        # Repetir a mesma requisicao e seguro: devolve o estado persistido sem
        # tentar dirigir o navegador novamente.
        return item
    if _block_pre_operation_reminder(db, item):
        db.commit()
        db.refresh(item)
        return item
    # A fila permanece pendente fora do expediente e sera retomada
    # automaticamente no proximo periodo comercial.
    if not is_whatsapp_business_hours():
        return item
    if settings.sync_hub_url:
        try:
            response = requests.post(
                settings.sync_hub_url.rstrip("/") + "/whatsapp/queue/direct",
                json={
                    "telefone": item.telefone,
                    "mensagem": item.mensagem,
                    "tipo_mensagem": item.tipo_mensagem,
                    "documento_id": item.documento_id,
                    "colaborador_id": item.colaborador_id,
                    "usuario_id": item.usuario_id,
                    "payload": {
                        **(item.payload or {}),
                        "origin": "desktop_forward_to_central",
                        "local_queue_id": item.id,
                    },
                },
                timeout=20,
            )
            response.raise_for_status()
            item.status = "encaminhado_hub"
            item.enviado_em = datetime.now()
            item.payload = {**(item.payload or {}), "central_queue": response.json()}
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
                    payload={"origin": "desktop_forward_to_central", "response": response.json()},
                )
            )
            return item
        except requests.RequestException as exc:
            mark_queue_failed(db, item=item, error=f"Falha ao encaminhar para HUB central: {exc}")
            return item

    sender = sender or WhatsAppWebSender()
    # API e worker podem solicitar o mesmo item ao mesmo tempo. O lock abrange
    # reserva, envio e commit: somente quem mudou pendente -> processando envia.
    with _interprocess_send_lock():
        claimed = (
            db.query(WhatsAppQueue)
            .filter(WhatsAppQueue.id == queue_id, WhatsAppQueue.status == "pendente")
            .update(
                {
                    WhatsAppQueue.status: "processando",
                    WhatsAppQueue.updated_at: datetime.now(),
                    WhatsAppQueue.ultimo_erro: None,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        db.expire_all()
        item = db.get(WhatsAppQueue, queue_id)
        if not item:
            raise ValueError("Item de fila nao encontrado.")
        if not claimed:
            return item

        try:
            result = sender.send(telefone=item.telefone, mensagem=item.mensagem, acquire_lock=False)
            item.status = "enviado"
            item.enviado_em = datetime.now()
            item.tentativas += result.attempts
            item.ultimo_erro = None
            item.motivo_bloqueio = None
            item.payload = {**(item.payload or {}), "send_result": result.__dict__}
            grouped_query = (
                db.query(WhatsAppQueue)
                .filter(
                    WhatsAppQueue.id != item.id,
                    WhatsAppQueue.telefone == item.telefone,
                    WhatsAppQueue.tipo_mensagem == item.tipo_mensagem,
                    WhatsAppQueue.status == "pendente",
                )
            )
            if item.colaborador_id:
                grouped_query = grouped_query.filter(WhatsAppQueue.colaborador_id == item.colaborador_id)
            grouped_items = grouped_query.all()
            grouped_ids = []
            for grouped in grouped_items:
                grouped.status = "agrupado"
                grouped.enviado_em = item.enviado_em
                grouped.ultimo_erro = f"Agrupado no envio WhatsApp #{item.id}"
                grouped.payload = {**(grouped.payload or {}), "grouped_into_queue_id": item.id}
                grouped_ids.append(grouped.id)
            db.add(
                WhatsAppHistory(
                    queue_id=item.id,
                    documento_id=item.documento_id,
                    colaborador_id=item.colaborador_id,
                    usuario_id=item.usuario_id,
                    telefone=item.telefone,
                    mensagem=item.mensagem,
                    status="enviado",
                    tipo_mensagem=item.tipo_mensagem,
                    payload={**result.__dict__, "grouped_queue_ids": grouped_ids},
                )
            )
            for grouped in grouped_items:
                db.add(
                    WhatsAppHistory(
                        queue_id=grouped.id,
                        documento_id=grouped.documento_id,
                        colaborador_id=grouped.colaborador_id,
                        usuario_id=grouped.usuario_id,
                        telefone=grouped.telefone,
                        mensagem=grouped.mensagem,
                        status="agrupado",
                        tipo_mensagem=grouped.tipo_mensagem,
                        payload={"grouped_into_queue_id": item.id},
                    )
                )
        except WhatsAppSendError as exc:
            mark_queue_failed(db, item=item, error=str(exc))
        db.commit()
        db.refresh(item)
        return item


def send_pending_batch(
    db: Session,
    *,
    limit: int,
    sender: WhatsAppWebSender | None = None,
) -> dict[str, Any]:
    if not is_whatsapp_business_hours():
        return {
            "processed": 0,
            "sent": 0,
            "failed": 0,
            "deferred": True,
            "reason": "fora do horario comercial (segunda a sexta, 08:00-18:00)",
            "items": [],
        }
    sender = sender or WhatsAppWebSender()
    items = (
        db.query(WhatsAppQueue)
        .filter(WhatsAppQueue.status == "pendente")
        .order_by(WhatsAppQueue.created_at.asc())
        .limit(limit)
        .all()
    )
    sent = 0
    failed = 0
    results = []
    for index, item in enumerate(items):
        updated = send_queue_item(db, queue_id=item.id, sender=sender)
        if updated.status == "enviado":
            sent += 1
        elif updated.status == "falha":
            failed += 1
        results.append({"id": updated.id, "status": updated.status, "erro": updated.ultimo_erro})
        time_sleep = settings.whatsapp_between_messages_seconds
        if time_sleep > 0 and index < len(items) - 1:
            import time

            time.sleep(time_sleep)
    return {"processed": len(items), "sent": sent, "failed": failed, "items": results}
