from __future__ import annotations


BLOCKED_ROLE_KEYWORDS = (
    "DIRETOR",
    "DIRETORA",
    "EXECUTIVO",
    "EXECUTIVA",
    "EXECUTIVO SENIOR",
    "EXECUTIVA SENIOR",
    "VICE-PRESIDENTE",
    "VICE PRESIDENTE",
    "PRESIDENTE",
    "PRESIDENTA",
    "CEO",
    "CFO",
    "CTO",
    "COO",
)


def blocked_role_reason(role: str | None) -> str | None:
    text = str(role or "").upper().strip()
    if not text:
        return None
    for keyword in BLOCKED_ROLE_KEYWORDS:
        if keyword in text:
            return f"Cargo bloqueado para envio automatico: {keyword}"
    return None
