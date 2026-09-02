from __future__ import annotations

import re
from typing import Any


PROFILE_DOCUMENT_MODELS = {
    "PERFORMANCE": "T14",
    "PERFORMANCE G2": "T14",
    "PERFORMANCE G4": "T14 Gen4",
    "PERFORMANCE G6": "T14Gen6",
}

FIVE_G_OPTIONS = ("LATITUDE 5450", "LATITUDE 5440")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_profile(value: Any) -> str:
    text = clean_text(value).upper()
    text = text.replace("PERFORMACE", "PERFORMANCE")
    text = re.sub(r"\s+", " ", text)
    return text


def is_automatos_technical_model(value: Any) -> bool:
    text = clean_text(value).upper()
    if not text:
        return False
    if re.fullmatch(r"(20|21|22)[A-Z0-9]{5,}", text):
        return True
    if re.fullmatch(r"LA\d+[A-Z0-9]+", text):
        return True
    if any(token in text for token in ("C3220", "BLACKWIRE", "PLANTRONICS", "HEADSET", "CARREGADOR", "FONTE")):
        return True
    return False


def model_from_profile(profile: Any) -> str:
    key = normalize_profile(profile)
    if key == "5G":
        return ""
    return PROFILE_DOCUMENT_MODELS.get(key, "")


def model_from_technical_model(model: Any) -> str:
    text = clean_text(model).upper()
    if not text:
        return ""
    if text.startswith("20W"):
        return "T14"
    if text.startswith("21H"):
        return "T14 Gen4"
    return ""


def resolve_document_model(
    *,
    profile: Any = None,
    documented_models: list[Any] | tuple[Any, ...] | None = None,
    automatos_model: Any = None,
    fallback_model: Any = None,
) -> dict[str, Any]:
    """Resolve o modelo que deve aparecer em documentos.

    Automatos traz o modelo tecnico de BIOS/SKU (ex.: 21HESHG000). Esse valor
    fica apenas como evidencia tecnica. O documento deve usar modelo comercial
    do perfil/termo, como T14, T14 Gen4, T14Gen6 ou Latitude.
    """

    profile_key = normalize_profile(profile)
    warnings: list[str] = []

    profile_model = model_from_profile(profile_key)
    if profile_model:
        return {
            "modelo": profile_model,
            "origem": "perfil",
            "perfil": profile_key,
            "opcoes": [],
            "warnings": warnings,
        }

    documented_models = list(documented_models or [])
    for model in documented_models:
        text = clean_text(model)
        if text and not is_automatos_technical_model(text):
            return {
                "modelo": text,
                "origem": "termo",
                "perfil": profile_key,
                "opcoes": [],
                "warnings": warnings,
            }

    if profile_key == "5G":
        warnings.append(
            "Perfil 5G pode ser LATITUDE 5450 ou LATITUDE 5440. Confirmar pelo termo mais recente."
        )
        return {
            "modelo": "",
            "origem": "revisao_5g",
            "perfil": profile_key,
            "opcoes": list(FIVE_G_OPTIONS),
            "warnings": warnings,
        }

    technical_model = model_from_technical_model(automatos_model)
    if technical_model:
        warnings.append(
            f"Modelo tecnico do Automatos ({clean_text(automatos_model)}) convertido para modelo documental."
        )
        return {
            "modelo": technical_model,
            "origem": "automatos_convertido",
            "perfil": profile_key,
            "opcoes": [],
            "warnings": warnings,
        }

    fallback = clean_text(fallback_model)
    if fallback and not is_automatos_technical_model(fallback):
        return {
            "modelo": fallback,
            "origem": "fallback",
            "perfil": profile_key,
            "opcoes": [],
            "warnings": warnings,
        }

    if automatos_model:
        warnings.append(
            f"Automatos trouxe apenas modelo tecnico ({clean_text(automatos_model)}). Confirmar modelo documental manualmente."
        )

    return {
        "modelo": "",
        "origem": "manual",
        "perfil": profile_key,
        "opcoes": [],
        "warnings": warnings,
    }
