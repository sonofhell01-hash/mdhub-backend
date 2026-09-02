import re
import unicodedata
from datetime import datetime
from typing import Any


class DataNormalizer:
    TICKET_PREFIXES = ("INC", "REQ", "LNR")

    @staticmethod
    def clean_key(value: Any) -> str:
        text = "" if value is None else str(value).strip()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(char for char in text if not unicodedata.combining(char))
        return re.sub(r"[^A-Z0-9]", "", text.upper())

    @staticmethod
    def parse_date(value: Any) -> str | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        text = str(value).strip()
        if not text or text.upper() in {"-", "N/A", "NA", "NAN"}:
            return None
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
            "%d%m%Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt).isoformat(sep=" ", timespec="seconds")
            except ValueError:
                pass
        return text

    @staticmethod
    def normalize_serial(value: Any) -> str | None:
        if not value:
            return None
        text = str(value).strip().upper()
        text = re.sub(r"^[*#@\s]+|[*#@\s]+$", "", text)
        key = DataNormalizer.clean_key(text)
        if not key or key in {"NA", "NAN", "N/A", "SEM SERIAL"}:
            return None
        if key.isdigit() and len(key) <= 3:
            return None
        return key

    @staticmethod
    def normalize_patrimonio(value: Any) -> str | None:
        key = DataNormalizer.clean_key(value)
        if not key or key in {"NA", "NAN", "N/A"}:
            return None
        return key

    @staticmethod
    def is_ticket_identifier(value: Any) -> bool:
        key = DataNormalizer.clean_key(value)
        return any(key.startswith(prefix) for prefix in DataNormalizer.TICKET_PREFIXES)

    @staticmethod
    def normalize_matricula(value: Any) -> str | None:
        key = DataNormalizer.clean_key(value)
        if not key or DataNormalizer.is_ticket_identifier(key):
            return None
        if key.isdigit() and 6 <= len(key) <= 8:
            return key
        if len(key) >= 7 and key[0] in {"F", "T"} and key[1:].isdigit():
            return key
        return None

    @staticmethod
    def matricula_key(value: Any) -> str | None:
        mat = DataNormalizer.normalize_matricula(value)
        if not mat:
            return None
        if len(mat) >= 7 and mat[0] in {"F", "T"} and mat[1:].isdigit():
            return mat[1:]
        return mat

    @staticmethod
    def normalize_hostname(value: Any) -> str | None:
        key = DataNormalizer.clean_key(value)
        return key or None

    @staticmethod
    def normalize_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.upper() in {"-", "N/A", "NA", "NAN"}:
            return None
        return text

    @staticmethod
    def normalize_phone(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.upper() in {"-", "N/A", "NA", "NAN"}:
            return None
        # Excel/CSV costuma usar apostrofo inicial para forcar texto.
        text = re.sub(r"^[`'\"´]+", "", text).strip()
        text = re.sub(r"\s+", " ", text)
        return text or None
