from __future__ import annotations

import re


def only_digits(value: str | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def format_brazil_whatsapp(value: str | None) -> str:
    """Return phone in 55 + DDD + number format, or empty when invalid."""
    digits = only_digits(value)
    if not digits:
        return ""

    if len(digits) == 13 and digits.startswith("55"):
        return digits

    if len(digits) == 12 and digits.startswith("55"):
        local = digits[2:]
        return f"55{local[:2]}9{local[2:]}"

    if len(digits) == 11:
        return f"55{digits}"

    if len(digits) == 10:
        return f"55{digits[:2]}9{digits[2:]}"

    if len(digits) > 13:
        return format_brazil_whatsapp(digits[-11:])

    return ""


def is_valid_whatsapp(value: str | None) -> bool:
    return bool(format_brazil_whatsapp(value))
