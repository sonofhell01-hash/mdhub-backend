import re

from src.core.config import settings
from src.services.ai.errors import AIInputRejected, AIInputTooLarge, AIInvalidResponse


SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:senha|password|token|api[_ -]?key|authorization)\s*[:=]", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~-]{10,}", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
)
HTML_PATTERN = re.compile(r"<\s*/?\s*[a-z][^>]*>", re.IGNORECASE)
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
PLACEHOLDER_VALUES = {
    "string",
    "example string",
    "sample string",
}


def validate_input(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        raise AIInputRejected("Texto obrigatorio")
    if value.casefold() in PLACEHOLDER_VALUES:
        raise AIInputRejected("Substitua o valor de exemplo pelo texto real")
    if len(value) > settings.ai_max_input_chars:
        raise AIInputTooLarge("Texto acima do limite do piloto")
    if HTML_PATTERN.search(value) or CONTROL_PATTERN.search(value):
        raise AIInputRejected("HTML ou conteudo binario nao permitido")
    if any(pattern.search(value) for pattern in SENSITIVE_PATTERNS):
        raise AIInputRejected("Conteudo sensivel nao permitido no piloto")
    return value


def validate_output(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        raise AIInvalidResponse("Resposta vazia")
    if "<think>" in value.lower() or "</think>" in value.lower():
        raise AIInvalidResponse("Resposta de raciocinio nao permitida")
    return value
