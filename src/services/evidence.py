from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from src.models.core import AssetEvidence, Equipment
from src.services.inventory.normalizer import DataNormalizer


def _text(*values: Any) -> str | None:
    for value in values:
        if value not in (None, ""):
            cleaned = str(value).strip()
            if cleaned:
                return cleaned
    return None


def _upper(*values: Any) -> str | None:
    value = _text(*values)
    return value.upper() if value else None


def _serial(*values: Any) -> str | None:
    value = _upper(*values)
    return DataNormalizer.normalize_serial(value) if value else None


def _matricula(*values: Any) -> str | None:
    value = _text(*values)
    return DataNormalizer.matricula_key(value) if value else None


def _parse_date(*values: Any) -> datetime | None:
    for value in values:
        parsed = DataNormalizer.parse_date(value)
        if parsed:
            if isinstance(parsed, datetime):
                if parsed.tzinfo:
                    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed
            try:
                parsed_dt = datetime.fromisoformat(str(parsed).replace("Z", "+00:00"))
                if parsed_dt.tzinfo:
                    return parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed_dt
            except ValueError:
                return None
    return None


def _external_id(row: dict[str, Any], fallback: str) -> str:
    for key in ("id", "machine_id", "asset_id", "document_id", "uuid"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return fallback


def upsert_evidence(db: Session, evidence: dict[str, Any]) -> AssetEvidence:
    fonte = str(evidence["fonte"])
    modulo = str(evidence["modulo"])
    external_id = _text(evidence.get("external_id")) or "|".join(
        _text(evidence.get(key)) or "" for key in ("serial", "hostname", "matricula")
    )

    record = (
        db.query(AssetEvidence)
        .filter(
            AssetEvidence.fonte == fonte,
            AssetEvidence.modulo == modulo,
            AssetEvidence.external_id == external_id,
        )
        .first()
    )
    if not record:
        record = AssetEvidence(fonte=fonte, modulo=modulo, external_id=external_id)
        db.add(record)

    for key in (
        "serial",
        "patrimonio",
        "hostname",
        "matricula",
        "nome",
        "email",
        "categoria",
        "marca",
        "modelo",
        "status",
        "confidence",
        "evidence_at",
        "payload",
    ):
        setattr(record, key, evidence.get(key))
    return record


def evidence_from_midiasimples(module: str, row: dict[str, Any]) -> dict[str, Any] | None:
    serial = _serial(
        row.get("serial"),
        row.get("product_serial_number"),
        row.get("product_serial"),
        row.get("new_product_serial_number"),
        row.get("old_product_serial_number"),
    )
    matricula = _matricula(row.get("registration"), row.get("matricula"), row.get("client_registration"))
    hostname = _upper(row.get("hostname"), row.get("host_name"))
    if not any((serial, matricula, hostname)):
        return None

    status_by_module = {
        "concessoes": "CONCEDIDO",
        "devolucoes": "DEVOLVIDO",
        "emprestimos": "EMPRESTIMO",
        "rats": "RAT",
        "laudos": "LAUDO",
    }
    return {
        "fonte": "midiasimples",
        "modulo": module,
        "external_id": _external_id(row, f"{module}:{serial or matricula or hostname}"),
        "serial": serial,
        "patrimonio": _upper(row.get("patrimony"), row.get("patrimonio"), row.get("asset_tag")),
        "hostname": hostname,
        "matricula": matricula,
        "nome": _text(row.get("name"), row.get("client_name"), row.get("collaborator_name")),
        "email": _text(row.get("email"), row.get("client_email")),
        "categoria": _upper(row.get("product_category"), row.get("category"), row.get("tipo")),
        "marca": _upper(row.get("product_brand"), row.get("brand"), row.get("marca")),
        "modelo": _text(row.get("product_model"), row.get("model"), row.get("modelo")),
        "status": status_by_module.get(module, module.upper()),
        "confidence": "alta" if module in {"concessoes", "devolucoes", "emprestimos"} else "media",
        "evidence_at": _parse_date(row.get("updated_at"), row.get("created_at"), row.get("date")),
        "payload": row,
    }


def evidence_from_automatos(row: dict[str, Any]) -> dict[str, Any] | None:
    serial = _serial(row.get("serial_number"), row.get("serial_number_key"), row.get("serial"))
    matricula = _matricula(row.get("top_user"), row.get("top_user_key"), row.get("current_user"), row.get("user"))
    hostname = _upper(row.get("computer_name"), row.get("computer_name_key"), row.get("hostname"))
    if not any((serial, matricula, hostname)):
        return None

    return {
        "fonte": "automatos",
        "modulo": "desktops",
        "external_id": _external_id(row, f"automatos:{serial or hostname or matricula}"),
        "serial": serial,
        "patrimonio": _upper(row.get("asset_tag"), row.get("patrimony")),
        "hostname": hostname,
        "matricula": matricula,
        "nome": None,
        "email": None,
        "categoria": _upper(row.get("type"), row.get("device_type")),
        "marca": _upper(row.get("manufacturer")),
        "modelo": _text(row.get("system_product_name"), row.get("model")),
        "status": _upper(row.get("status")) or "OBSERVADO",
        "confidence": "alta",
        "evidence_at": _parse_date(row.get("collect_date"), row.get("update_date"), row.get("updated_at")),
        "payload": row,
    }


def ingest_midiasimples_rows(db: Session, module: str, rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        evidence = evidence_from_midiasimples(module, row)
        if evidence:
            upsert_evidence(db, evidence)
            count += 1
    db.commit()
    return count


def ingest_automatos_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        evidence = evidence_from_automatos(row)
        if evidence:
            upsert_evidence(db, evidence)
            _upsert_equipment_from_automatos(db, evidence)
            count += 1
    db.commit()
    return count


def _upsert_equipment_from_automatos(db: Session, evidence: dict[str, Any]) -> None:
    serial = evidence.get("serial")
    if not serial:
        return
    equipment = db.query(Equipment).filter(Equipment.serial == serial).first()
    if not equipment:
        equipment = Equipment(serial=serial, fonte="automatos_api")
        db.add(equipment)
    equipment.hostname = evidence.get("hostname") or equipment.hostname
    equipment.marca = evidence.get("marca") or equipment.marca
    equipment.modelo_tecnico = evidence.get("modelo") or equipment.modelo_tecnico
    equipment.categoria = evidence.get("categoria") or equipment.categoria
    equipment.status = "observado_automatos"
    equipment.payload = evidence.get("payload")
