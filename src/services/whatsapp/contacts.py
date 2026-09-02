from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.models.core import Collaborator, WhatsAppContact
from src.services.whatsapp.phone_utils import format_brazil_whatsapp


def find_contact(db: Session, query: str) -> WhatsAppContact | None:
    q = str(query or "").strip()
    if not q:
        return None
    phone = format_brazil_whatsapp(q)
    filters = [
        WhatsAppContact.matricula == q,
        WhatsAppContact.email.ilike(f"%{q}%"),
        WhatsAppContact.nome.ilike(f"%{q}%"),
    ]
    if phone:
        filters.append(WhatsAppContact.telefone_formatado == phone)
    return db.query(WhatsAppContact).filter(or_(*filters)).order_by(WhatsAppContact.updated_at.desc()).first()


def upsert_contact(
    db: Session,
    *,
    matricula: str | None,
    nome: str | None,
    telefone: str | None,
    email: str | None = None,
    cargo: str | None = None,
    fonte: str = "manual",
    payload: dict[str, Any] | None = None,
) -> WhatsAppContact | None:
    formatted = format_brazil_whatsapp(telefone)
    if not formatted:
        return None

    collaborator = None
    if matricula:
        collaborator = db.query(Collaborator).filter(Collaborator.matricula == str(matricula).strip()).first()

    contact = db.query(WhatsAppContact).filter(WhatsAppContact.telefone_formatado == formatted).first()
    if not contact:
        contact = WhatsAppContact(telefone_formatado=formatted)
        db.add(contact)

    contact.colaborador_id = collaborator.id if collaborator else contact.colaborador_id
    contact.matricula = str(matricula).strip() if matricula else contact.matricula
    contact.nome = nome or contact.nome
    contact.email = email or contact.email
    contact.cargo = cargo or contact.cargo
    contact.telefone = telefone or contact.telefone
    contact.fonte = fonte
    contact.status = "ativo"
    contact.payload = payload or contact.payload
    db.flush()
    return contact


def import_contacts_csv(db: Session, path: str | Path, *, fonte: str = "sap_report") -> dict[str, int]:
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(str(csv_path))

    created_or_updated = 0
    ignored = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            contact = upsert_contact(
                db,
                matricula=row.get("u_matricula") or row.get("matricula") or row.get("registration"),
                nome=row.get("u_nome_completo") or row.get("nome") or row.get("name"),
                telefone=row.get("whatsapp") or row.get("u_numcelular") or row.get("telefone"),
                email=row.get("u_emailtim") or row.get("email"),
                cargo=row.get("u_cargorh") or row.get("cargo"),
                fonte=fonte,
                payload={"row": row},
            )
            if contact:
                created_or_updated += 1
            else:
                ignored += 1
    return {"processed": created_or_updated + ignored, "upserted": created_or_updated, "ignored": ignored}
