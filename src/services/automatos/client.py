from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from src.core.config import settings


AUTOMATOS_HARDWARE_PATH = "/api/public/api/getAllHardware/desktops"


class AutomatosApiError(RuntimeError):
    pass


@dataclass
class AutomatosSnapshot:
    path: str
    rows: list[dict[str, Any]]

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def cursor(self) -> str | None:
        latest = self.latest_row()
        if not latest:
            return None
        return automatos_cursor(latest) or None

    @property
    def latest_collect_date(self) -> str | None:
        latest = self.latest_row()
        if not latest:
            return None
        value = latest.get("collect_date")
        return str(value) if value not in (None, "") else None

    @property
    def latest_update_date(self) -> str | None:
        latest = self.latest_row()
        if not latest:
            return None
        value = latest.get("update_date") or latest.get("updated_at")
        return str(value) if value not in (None, "") else None

    def latest_row(self) -> dict[str, Any] | None:
        if not self.rows:
            return None
        return max(self.rows, key=automatos_sort_key)

    def sample(self, length: int = 5) -> list[dict[str, Any]]:
        rows = sorted(self.rows, key=automatos_sort_key, reverse=True)
        return [automatos_sample(row) for row in rows[:length]]


class AutomatosClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        automatos_id: str | None = None,
        security_key: str | None = None,
        timeout: int = 45,
    ) -> None:
        self.base_url = (base_url or settings.automatos_base_url).rstrip("/")
        self.automatos_id = automatos_id if automatos_id is not None else settings.automatos_id
        self.security_key = security_key if security_key is not None else settings.automatos_security_key
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.automatos_id and self.security_key)

    def get_desktops(self) -> AutomatosSnapshot:
        if not self.configured:
            raise AutomatosApiError("AUTOMATOS_ID/AUTOMATOS_SECURITY_KEY nao configurados.")

        url = self.base_url + AUTOMATOS_HARDWARE_PATH
        try:
            response = requests.get(
                url,
                params={
                    "AutomatosId": self.automatos_id,
                    "Securitykey": self.security_key,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise AutomatosApiError(f"Falha ao consultar Automatos: {exc}") from exc
        except ValueError as exc:
            raise AutomatosApiError("Resposta Automatos nao e JSON valido.") from exc

        return AutomatosSnapshot(path=AUTOMATOS_HARDWARE_PATH, rows=automatos_rows(payload))


def automatos_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "desktops", "hardware", "result", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = automatos_rows(value)
            if nested:
                return nested
    return []


def automatos_cursor(row: dict[str, Any]) -> str:
    for key in (
        "machine_id",
        "id",
        "asset_id",
        "computer_name",
        "computer_name_key",
        "serial_number",
        "serial_number_key",
        "collect_date",
        "update_date",
    ):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def automatos_sort_key(row: dict[str, Any]) -> tuple[datetime, str]:
    date_value = _parse_automatos_date(row.get("collect_date")) or _parse_automatos_date(
        row.get("update_date") or row.get("updated_at")
    )
    return (date_value or datetime.min, automatos_cursor(row))


def _parse_automatos_date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def automatos_sample(row: dict[str, Any]) -> dict[str, Any]:
    preferred = (
        "computer_name",
        "computer_name_key",
        "top_user",
        "top_user_key",
        "serial_number",
        "serial_number_key",
        "manufacturer",
        "system_product_name",
        "operating_system",
        "collect_date",
        "update_date",
        "status",
        "machine_id",
    )
    compact = {key: row.get(key) for key in preferred if row.get(key) not in (None, "")}
    return compact or {key: value for key, value in row.items() if key != "action"}
