from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.core.database import connect
from src.services.inventory.normalizer import DataNormalizer


AUTOMATOS_COLUMNS = (
    "computer_name",
    "computer_name_key",
    "serial",
    "patrimonio",
    "top_user",
    "top_user_key",
    "last_login",
    "collect_date",
    "update_date",
    "status",
    "computer_type",
    "manufacturer",
    "system_product_name",
    "operating_system",
    "processor",
    "memory",
    "installed_mem",
    "disk_total",
    "disk_used",
    "ip_address",
    "machine_id",
    "regional_scope",
    "raw_payload",
    "synced_at",
)


def _text(*values: Any) -> str | None:
    for value in values:
        if value not in (None, ""):
            cleaned = str(value).strip()
            if cleaned and cleaned.upper() not in {"-", "N/A", "NA", "NAN"}:
                return cleaned
    return None


def _serial(*values: Any) -> str | None:
    for value in values:
        serial = DataNormalizer.normalize_serial(value)
        if serial:
            return serial
    return None


def _key(value: Any) -> str | None:
    cleaned = DataNormalizer.clean_key(value)
    return cleaned or None


def _matricula_key(*values: Any) -> str | None:
    for value in values:
        matricula = DataNormalizer.matricula_key(value)
        if matricula:
            return matricula
    return None


def _date(*values: Any) -> str | None:
    for value in values:
        parsed = DataNormalizer.parse_date(value)
        if parsed:
            return parsed
    return None


def _scope(row: dict[str, Any], computer_name_key: str | None, top_user_key: str | None) -> str:
    scope = _text(row.get("regional_scope"), row.get("scope"))
    if scope:
        return scope.upper()
    if computer_name_key and computer_name_key.startswith("NAKRJ"):
        return "RJ_CEO"
    if top_user_key and top_user_key.startswith("80"):
        return "RJ_CEO"
    return "OUTRAS_REGIONAIS"


def _snapshot_row(row: dict[str, Any], synced_at: str) -> dict[str, Any]:
    computer_name = _text(row.get("computer_name"), row.get("hostname"))
    computer_name_key = _key(row.get("computer_name_key") or computer_name)
    serial = _serial(
        row.get("serial"),
        row.get("serial_number"),
        row.get("serial_number_key"),
        row.get("system_serial_number"),
    )
    top_user = _text(row.get("top_user"), row.get("current_user"), row.get("user"))
    top_user_key = _matricula_key(row.get("top_user_key"), top_user)
    raw_payload = json.dumps(row, ensure_ascii=False, default=str)

    return {
        "computer_name": computer_name,
        "computer_name_key": computer_name_key,
        "serial": serial,
        "patrimonio": DataNormalizer.normalize_patrimonio(
            _text(row.get("patrimonio"), row.get("patrimony"), row.get("asset_tag"))
        ),
        "top_user": top_user,
        "top_user_key": top_user_key,
        "last_login": _text(row.get("last_login"), row.get("login_name")),
        "collect_date": _date(row.get("collect_date")),
        "update_date": _date(row.get("update_date"), row.get("updated_at")),
        "status": _text(row.get("status")),
        "computer_type": _text(row.get("computer_type"), row.get("type"), row.get("device_type")),
        "manufacturer": _text(row.get("manufacturer"), row.get("system_manufacturer")),
        "system_product_name": _text(row.get("system_product_name"), row.get("model")),
        "operating_system": _text(row.get("operating_system"), row.get("so_string")),
        "processor": _text(row.get("processor"), row.get("cpu_identity")),
        "memory": _text(row.get("memory"), row.get("memory_range")),
        "installed_mem": _text(row.get("installed_mem")),
        "disk_total": _text(row.get("disk_total")),
        "disk_used": _text(row.get("disk_used")),
        "ip_address": _text(row.get("ip_address"), row.get("machine_net_ipaddress")),
        "machine_id": _text(row.get("machine_id"), row.get("id"), row.get("asset_id")),
        "regional_scope": _scope(row, computer_name_key, top_user_key),
        "raw_payload": raw_payload,
        "synced_at": synced_at,
    }


def sync_automatos_snapshot(rows: list[dict[str, Any]]) -> int:
    """Atualiza a tabela legada usada pela Consulta Operacional.

    O HUB novo grava evidencias centrais, mas os modulos operacionais ainda
    pesquisam `automatos_snapshot`. Este sincronismo evita que eles continuem
    dependentes do snapshot antigo.
    """
    if not rows:
        return 0

    synced_at = datetime.now().isoformat(sep=" ", timespec="seconds")
    placeholders = ", ".join("?" for _ in AUTOMATOS_COLUMNS)
    columns_sql = ", ".join(AUTOMATOS_COLUMNS)
    batch: list[list[Any]] = []

    with connect() as conn:
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            item = _snapshot_row(raw, synced_at)
            if not any((item["machine_id"], item["serial"], item["computer_name_key"])):
                continue

            batch.append([item[column] for column in AUTOMATOS_COLUMNS])
        if batch:
            conn.execute("DELETE FROM automatos_snapshot")
            conn.executemany(
                f"INSERT INTO automatos_snapshot ({columns_sql}) VALUES ({placeholders})",
                batch,
            )
        conn.commit()

    return len(batch)
