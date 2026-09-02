from __future__ import annotations

import re
import unicodedata
import urllib.parse
import urllib.request
from io import BytesIO
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from src.models.core import Collaborator, Document, WhatsAppHistory, WhatsAppQueue
from src.services.midiasimples.session_store import get_validated_session
from src.services.whatsapp.queue import ACTIVE_QUEUE_STATUSES, queue_document_reminder


MIN_DOCUMENT_AGE_SECONDS = 3600

SIGNATURE_DOCUMENT_CONFIGS = (
    {"tipo": "rat", "path": "/rat-attendance", "label": "RAT"},
    {"tipo": "laudo", "path": "/laudo-tecnico-tim", "label": "Laudo"},
    {"tipo": "concessao", "path": "/colaboradores-tim", "label": "Concessao"},
    {"tipo": "emprestimo", "path": "/loan-term", "label": "Emprestimo"},
    {"tipo": "devolucao", "path": "/termo-de-devolucao", "label": "Devolucao"},
)

SIGNATURE_RADAR_TECHNICIANS = (
    "MARCEL DIEGO SILVA",
    "CAIO VINICIUS PEREIRA DA SILVA FREITAS",
    "MICHEL PURCINA DELOCCO",
    "MARCOS PAULO DA SILVA REIS",
)

# Nome antigo mantido para compatibilidade com rotas e scripts existentes.
ALLOWED_RAT_TECHNICIANS = set(SIGNATURE_RADAR_TECHNICIANS)

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


def normalize_name(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def rat_responsible(row: dict[str, Any]) -> str:
    return str(
        pick(
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


def rat_signature_status(row: dict[str, Any]) -> str:
    return str(
        pick(
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


def technician_signature_status(row: dict[str, Any]) -> str:
    return str(
        pick(
            row,
            "technician_signature_status",
            "tech_signature_status",
            "tecnico_signature_status",
            "technical_signature_status",
            "arklok_signature_status",
            "responsible_signature_status",
            "status_assinatura_tecnico",
            "assinatura_tecnico",
            "assinatura_responsavel",
            "tecnico_assinatura",
        )
        or ""
    ).strip()


def customer_signature_status(row: dict[str, Any]) -> str:
    return str(
        pick(
            row,
            "customer_signature_status",
            "client_signature_status",
            "user_signature_status",
            "colaborador_signature_status",
            "status_assinatura_cliente",
            "status_assinatura_usuario",
            "assinatura_cliente",
            "assinatura_usuario",
            "assinatura_colaborador",
        )
        or ""
    ).strip()


def rat_is_signed(row: dict[str, Any]) -> bool:
    status = normalize_name(rat_signature_status(row))
    if not status:
        return False
    return status in SIGNED_STATUS_KEYS or "COMPLET" in status or "ASSINAD" in status


def status_is_signed(value: Any) -> bool:
    status = normalize_name(value)
    if not status:
        return False
    truthy_values = {"1", "TRUE", "SIM", "YES", "OK"}
    return status in SIGNED_STATUS_KEYS or status in truthy_values or "COMPLET" in status or "ASSINAD" in status


def signature_gate(row: dict[str, Any]) -> tuple[bool, str]:
    if rat_is_signed(row):
        return False, "documento ja assinado por completo"

    tech_status = technician_signature_status(row)
    customer_status = customer_signature_status(row)

    if tech_status and not status_is_signed(tech_status):
        return False, "aguardando assinatura do tecnico"
    if customer_status and status_is_signed(customer_status):
        return False, "assinatura do usuario ja consta como concluida"
    if tech_status and customer_status:
        return True, "usuario pendente com tecnico assinado"

    return False, "status da assinatura do tecnico nao confirmado"


def _signature_pdf_url_candidates(session: Any, document_path: str, external_id: str) -> list[str]:
    encoded_id = urllib.parse.quote(external_id)
    base = f"{session.base_url}{document_path}/{encoded_id}"
    urls: list[str] = []
    if document_path == "/colaboradores-tim":
        urls.append(f"{base}/docusign?archive=combined")
    urls.extend((f"{base}/docusign/download", f"{base}/download/docusign", f"{base}/download"))
    return urls


def _download_signature_pdf(session: Any, row: dict[str, Any], document_path: str) -> tuple[bytes | None, str]:
    external_id = str(pick(row, "id") or "").strip()
    envelope_id = str(pick(row, "docusign_id") or "").strip()
    if not external_id or not envelope_id:
        return None, "status da assinatura do tecnico nao confirmado"

    last_error = "consulta de assinaturas nao retornou PDF"
    for url in _signature_pdf_url_candidates(session, document_path, external_id):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "MD-HUB-FINAL/2026",
                "Accept": "application/pdf",
                "Referer": f"{session.base_url}{document_path}",
            },
        )
        try:
            with session.opener.open(request, timeout=45) as response:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                pdf_bytes = response.read(20 * 1024 * 1024 + 1)
                if "application/pdf" not in content_type and not pdf_bytes.startswith(b"%PDF"):
                    last_error = "consulta de assinaturas nao retornou PDF"
                    continue
                if len(pdf_bytes) > 20 * 1024 * 1024:
                    return None, "PDF de assinaturas excedeu 20 MB"
                return pdf_bytes, "ok"
        except Exception as exc:
            last_error = f"falha ao consultar assinaturas individuais: {type(exc).__name__}"
    return None, last_error


def _signature_label_role(words: list[dict[str, Any]], label: dict[str, Any], customer_name: str) -> str | None:
    label_top = float(label.get("top") or 0)
    context = " ".join(
        str(word.get("text") or "")
        for word in words
        if label_top - 55 <= float(word.get("top") or 0) <= label_top + 55
    )
    normalized_context = normalize_name(context)
    raw_context = context.upper()

    # Campos reais dos modelos carregam placeholders como /user_sign/ ou
    # /ArklokSign/. Isso evita contar a palavra "assinatura" dentro de clausulas.
    has_signature_placeholder = bool(re.search(r"/[^/]*(?:SIGN|ASSIN)[^/]*/", raw_context))
    if not has_signature_placeholder:
        return None
    if any(marker in normalized_context for marker in ("ANALISTA", "ARKLOK", "TECNICO", "TECHNICIAN")):
        return "technician"
    if (
        any(marker in normalized_context for marker in ("USUARIO", "COLABORADOR"))
        or (customer_name and customer_name in normalized_context)
    ):
        return "customer"
    return None


def _pdf_signature_evidence(pdf_bytes: bytes, row: dict[str, Any]) -> dict[str, int]:
    import pdfplumber

    evidence = {
        "customer_required": 0,
        "customer_signed": 0,
        "technician_required": 0,
        "technician_signed": 0,
    }
    customer_name = normalize_name(pick(row, "name", "customer_name", "client_name", "nome", "colaborador"))
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words() or []
            labels = [word for word in words if normalize_name(word.get("text")).startswith("ASSINATURA")]
            usable_images = [image for image in page.images if float(image.get("top") or 0) >= 100]
            used_images: set[int] = set()
            for label in labels:
                role = _signature_label_role(words, label, customer_name)
                if not role:
                    continue
                evidence[f"{role}_required"] += 1
                label_top = float(label.get("top") or 0)
                label_bottom = float(label.get("bottom") or label_top)
                label_x0 = float(label.get("x0") or 0)
                label_x1 = float(label.get("x1") or label_x0)
                matches: list[tuple[float, int]] = []
                for image_index, image in enumerate(usable_images):
                    if image_index in used_images:
                        continue
                    image_top = float(image.get("top") or 0)
                    image_bottom = float(image.get("bottom") or image_top)
                    image_x0 = float(image.get("x0") or 0)
                    image_x1 = float(image.get("x1") or image_x0)
                    close_vertically = image_bottom >= label_top - 70 and image_top <= label_bottom + 110
                    overlaps_horizontally = image_x0 <= label_x1 + 220 and image_x1 >= label_x0 - 30
                    if close_vertically and overlaps_horizontally:
                        distance = min(abs(image_top - label_bottom), abs(label_top - image_bottom))
                        matches.append((distance, image_index))
                if matches:
                    _, matched_index = min(matches)
                    used_images.add(matched_index)
                    evidence[f"{role}_signed"] += 1
    return evidence


def combined_pdf_signature_gate(
    session: Any,
    row: dict[str, Any],
    *,
    document_path: str = "/colaboradores-tim",
) -> tuple[bool, str]:
    """Confere, no PDF parcial, cada campo obrigatorio do usuario e do tecnico."""
    pdf_bytes, download_reason = _download_signature_pdf(session, row, document_path)
    if not pdf_bytes:
        return False, download_reason

    try:
        evidence = _pdf_signature_evidence(pdf_bytes, row)
    except Exception as exc:
        return False, f"falha ao analisar PDF de assinaturas: {type(exc).__name__}"

    customer_required = evidence["customer_required"]
    customer_signed = evidence["customer_signed"]
    technician_required = evidence["technician_required"]
    technician_signed = evidence["technician_signed"]
    if customer_required and customer_signed >= customer_required:
        return False, "todas as assinaturas do usuario ja constam no PDF"
    if technician_required and technician_signed >= technician_required and customer_signed < customer_required:
        return True, (
            "usuario pendente com tecnico assinado no PDF "
            f"({customer_signed}/{customer_required} assinaturas do usuario)"
        )
    if not technician_required:
        return False, "status da assinatura do tecnico nao confirmado no PDF"
    return False, "aguardando assinatura do tecnico no PDF"


def allowed_rat_technician(row: dict[str, Any]) -> bool:
    responsible = normalize_name(rat_responsible(row))
    if not responsible:
        return False
    return any(
        wanted == responsible or wanted in responsible or responsible in wanted
        for wanted in (normalize_name(name) for name in SIGNATURE_RADAR_TECHNICIANS)
    )


def extract_called_number(row: dict[str, Any]) -> str | None:
    value = pick(row, "ticket", "numero_chamado", "call_number", "incident", "chamado")
    if value:
        return str(value).strip().upper()
    action = str(row.get("action") or "")
    match = re.search(r"(INC|REQ|LNR)\d{5,}", action, re.IGNORECASE)
    return match.group(0).upper() if match else None


def parse_midiasimples_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_cutoff_date(value: str | date | datetime | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return datetime.combine(value.date(), time.min)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    parsed = parse_midiasimples_date(value)
    if parsed:
        return datetime.combine(parsed.date(), time.min)
    return None


def rat_created_at(row: dict[str, Any]) -> datetime | None:
    return parse_midiasimples_date(
        pick(
            row,
            "created_at",
            "created",
            "creation_date",
            "created_date",
            "data_criacao",
            "data_criação",
            "date",
        )
    )


def document_is_too_recent(row: dict[str, Any], minimum_age_seconds: int) -> tuple[bool, int]:
    created_at = rat_created_at(row)
    if not created_at:
        return True, minimum_age_seconds
    remaining = int(((created_at + timedelta(seconds=minimum_age_seconds)) - datetime.now()).total_seconds())
    return remaining > 0, max(remaining, 0)


def document_rat_created_at(document: Document) -> datetime | None:
    payload = document.payload or {}
    dados = payload.get("dados") or {}
    row = dados.get("midiasimples") or {}
    if isinstance(row, dict):
        created_at = rat_created_at(row)
        if created_at:
            return created_at
    return document.created_at


def rat_is_before_cutoff(row: dict[str, Any], cutoff: datetime | None) -> bool:
    if not cutoff:
        return False
    created_at = rat_created_at(row)
    return created_at is None or created_at < cutoff


def upsert_document_from_row(db: Session, row: dict[str, Any], *, tipo: str) -> Document:
    external_id = str(pick(row, "id") or "").strip()
    if not external_id:
        raise ValueError(f"{tipo.upper()} sem ID externo do MidiaSimples.")

    matricula = str(pick(row, "registration", "matricula", "registro") or "").strip().lstrip("F")
    nome = str(pick(row, "name", "client_name", "nome", "colaborador") or matricula or "Colaborador").strip()
    email = str(pick(row, "email", "client_email") or "").strip() or None
    cargo = str(pick(row, "profile", "cargo") or "").strip() or None
    serial = str(pick(row, "serial", "product_serial_number") or "").strip() or None
    created_at = rat_created_at(row)

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

    document = db.query(Document).filter(Document.tipo == tipo, Document.midiasimples_id == external_id).first()
    payload = {
        "colaborador": {
            "matricula": matricula or None,
            "nome": nome,
            "email": email,
            "cargo": cargo,
        },
        "dados": {
            "tecnico": {"nome": rat_responsible(row)},
            "midiasimples": row,
            tipo: {
                "signature_status": rat_signature_status(row),
                "technician_signature_status": technician_signature_status(row),
                "customer_signature_status": customer_signature_status(row),
                "responsible": rat_responsible(row),
            },
            "equipamento_atual": {"serial": serial},
        },
    }
    if document:
        document.colaborador_id = collaborator.id if collaborator else document.colaborador_id
        document.numero_chamado = extract_called_number(row) or document.numero_chamado
        document.status = "pronto_envio"
        document.payload = payload
        return document

    document = Document(
        tipo=tipo,
        colaborador_id=collaborator.id if collaborator else None,
        numero_chamado=extract_called_number(row),
        midiasimples_id=external_id,
        status="pronto_envio",
        payload=payload,
        sync_pendente=False,
    )
    if created_at:
        document.created_at = created_at
    db.add(document)
    db.flush()
    return document


def upsert_rat_document_from_row(db: Session, row: dict[str, Any]) -> Document:
    return upsert_document_from_row(db, row, tipo="rat")


def _last_reminder_at(item: WhatsAppQueue) -> datetime | None:
    value = item.enviado_em or item.created_at
    if value and value.tzinfo:
        return value.replace(tzinfo=None)
    return value


def _wait_seconds_until_next_reminder(item: WhatsAppQueue, cooldown_seconds: int) -> int:
    last_reminder = _last_reminder_at(item)
    if not last_reminder:
        return 0
    next_allowed = last_reminder + timedelta(seconds=cooldown_seconds)
    remaining = int((next_allowed - datetime.now()).total_seconds())
    return max(remaining, 0)


def sync_rat_signature_reminders(
    db: Session,
    *,
    email: str,
    max_pages: int = 30,
    page_size: int = 100,
    queue_limit: int = 200,
    reminder_cooldown_seconds: int = 1800,
    rat_created_from: str | date | datetime | None = None,
    minimum_document_age_seconds: int = MIN_DOCUMENT_AGE_SECONDS,
    force: bool = False,
) -> dict[str, Any]:
    stored = get_validated_session(email, path="/rat-attendance")
    if not stored:
        raise ValueError("Sessao MidiaSimples nao esta ativa para este tecnico.")

    scanned = 0
    eligible = 0
    signed = 0
    out_of_scope = 0
    out_of_date = 0
    queued = 0
    waiting_cooldown = 0
    waiting_min_age = 0
    waiting_technician_signature = 0
    skipped: list[dict[str, Any]] = []
    queued_items: list[dict[str, Any]] = []
    cutoff = parse_cutoff_date(rat_created_from)

    for config in SIGNATURE_DOCUMENT_CONFIGS:
        tipo = str(config["tipo"])
        path = str(config["path"])
        for page in range(max_pages):
            start = page * page_size
            payload = stored.session.datatable(
                path,
                search="",
                start=start,
                length=page_size,
                order_by_id_desc=True,
            )
            rows = payload.get("data") or []
            if not rows:
                break

            for row in rows:
                scanned += 1
                if not allowed_rat_technician(row):
                    out_of_scope += 1
                    continue
                if rat_is_before_cutoff(row, cutoff):
                    out_of_date += 1
                    skipped.append(
                        {
                            "document_type": tipo,
                            "doc_id": pick(row, "id"),
                            "reason": f"documento anterior a {cutoff.date().isoformat()}" if cutoff else "documento sem data valida",
                        }
                    )
                    continue
                if rat_is_signed(row):
                    signed += 1
                    continue

                too_recent, wait_age_seconds = document_is_too_recent(row, minimum_document_age_seconds)
                if too_recent:
                    waiting_min_age += 1
                    skipped.append(
                        {
                            "document_type": tipo,
                            "doc_id": pick(row, "id"),
                            "reason": f"aguardando {wait_age_seconds}s para completar 1h da criacao",
                        }
                    )
                    continue

                can_send, gate_reason = signature_gate(row)
                if not can_send and gate_reason == "status da assinatura do tecnico nao confirmado":
                    can_send, gate_reason = combined_pdf_signature_gate(
                        stored.session,
                        row,
                        document_path=path,
                    )
                if not can_send:
                    if "tecnico" in gate_reason:
                        waiting_technician_signature += 1
                    skipped.append({"document_type": tipo, "doc_id": pick(row, "id"), "reason": gate_reason})
                    continue

                eligible += 1
                try:
                    document = upsert_document_from_row(db, row, tipo=tipo)
                except ValueError as exc:
                    skipped.append({"document_type": tipo, "doc_id": pick(row, "id"), "reason": str(exc)})
                    continue

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
                if not existing:
                    external_id = str(pick(row, "id") or "").strip()
                    if external_id:
                        # Um envio manual pode estar ligado a outro documento local,
                        # mas a mensagem/payload ainda referencia o mesmo ID externo.
                        existing = (
                            db.query(WhatsAppQueue)
                            .filter(
                                WhatsAppQueue.colaborador_id == document.colaborador_id,
                                WhatsAppQueue.tipo_mensagem == "lembrete_assinatura",
                                WhatsAppQueue.status.in_(ACTIVE_QUEUE_STATUSES),
                                WhatsAppQueue.mensagem.contains(external_id),
                            )
                            .order_by(WhatsAppQueue.created_at.desc())
                            .first()
                        )
                if existing:
                    if existing.status in ("pendente", "bloqueado", "em_envio"):
                        skipped.append(
                            {
                                "document_id": document.id,
                                "queue_id": existing.id,
                                "reason": f"ja existe lembrete {existing.status}",
                            }
                        )
                        continue

                    wait_seconds = _wait_seconds_until_next_reminder(existing, reminder_cooldown_seconds)
                    if wait_seconds > 0:
                        waiting_cooldown += 1
                        skipped.append(
                            {
                                "document_id": document.id,
                                "queue_id": existing.id,
                                "reason": f"aguardando {wait_seconds}s para novo lembrete",
                            }
                        )
                        continue

                if queued >= queue_limit:
                    skipped.append({"document_id": document.id, "reason": "limite de fila atingido"})
                    continue

                try:
                    item = queue_document_reminder(
                        db,
                        document_id=document.id,
                        force=force,
                        aggregate_pending=False,
                    )
                except ValueError as exc:
                    skipped.append({"document_id": document.id, "reason": str(exc)})
                    continue

                queued += 1
                collaborator_payload = (document.payload or {}).get("colaborador") or {}
                if not isinstance(collaborator_payload, dict):
                    collaborator_payload = {}
                nome = str(
                    collaborator_payload.get("nome")
                    or pick(row, "name", "client_name", "nome", "colaborador")
                    or "Colaborador"
                ).strip()
                created_at = rat_created_at(row)
                queued_items.append(
                    {
                        "document_id": document.id,
                        "queue_id": item.id,
                        "status": item.status,
                        "telefone": item.telefone,
                        "document_type": tipo,
                        "rat_id": document.midiasimples_id if tipo == "rat" else None,
                        "document_id_external": document.midiasimples_id,
                        "numero_chamado": document.numero_chamado,
                        "nome": nome,
                        "rat_created_at": created_at.isoformat() if created_at else None,
                        "responsavel": rat_responsible(row),
                    }
                )

            # As tabelas chegam por ID decrescente. Depois de uma pagina
            # inteiramente anterior ao corte, nao ha motivo para consultar as antigas.
            page_dates = [rat_created_at(row) for row in rows]
            if cutoff and page_dates and all(value is not None and value < cutoff for value in page_dates):
                break

    return {
        "scanned": scanned,
        "eligible": eligible,
        "signed": signed,
        "out_of_scope": out_of_scope,
        "out_of_date": out_of_date,
        "queued": queued,
        "waiting_cooldown": waiting_cooldown,
        "waiting_min_age": waiting_min_age,
        "waiting_technician_signature": waiting_technician_signature,
        "skipped": len(skipped),
        "document_types": [str(config["tipo"]) for config in SIGNATURE_DOCUMENT_CONFIGS],
        "allowed_technicians": list(SIGNATURE_RADAR_TECHNICIANS),
        "items": queued_items,
        "skipped_items": skipped[:100],
    }


def block_old_pending_rat_reminders(
    db: Session,
    *,
    rat_created_from: str | date | datetime | None,
) -> dict[str, Any]:
    cutoff = parse_cutoff_date(rat_created_from)
    if not cutoff:
        return {"blocked": 0, "cutoff": None}

    items = (
        db.query(WhatsAppQueue)
        .join(Document, WhatsAppQueue.documento_id == Document.id)
        .filter(
            WhatsAppQueue.tipo_mensagem == "lembrete_assinatura",
            WhatsAppQueue.status == "pendente",
        )
        .all()
    )
    blocked = 0
    for item in items:
        document = db.get(Document, item.documento_id) if item.documento_id else None
        if not document:
            continue
        created_at = document_rat_created_at(document)
        if created_at and created_at >= cutoff:
            continue

        blocked += 1
        item.status = "bloqueado"
        item.motivo_bloqueio = f"Documento anterior a {cutoff.date().isoformat()} nao recebe lembrete automatico."
        item.ultimo_erro = item.motivo_bloqueio
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
                motivo_bloqueio=item.motivo_bloqueio,
                payload={"origin": "block_old_pending_rat_reminders", "rat_created_from": cutoff.date().isoformat()},
            )
        )

    return {"blocked": blocked, "cutoff": cutoff.date().isoformat()}
