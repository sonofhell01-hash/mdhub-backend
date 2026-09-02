from typing import Any

from .normalizer import DataNormalizer


ASSET_CLASSES = ["PRINCIPAL", "PERIFERICO", "DIVERSO"]
PRINCIPAL_TYPES = ["NOTEBOOK", "MONITOR"]
PERIPHERAL_TYPES = [
    "HEADSET",
    "DOCK",
    "FONTE",
    "MOUSE",
    "TECLADO",
    "WEBCAM",
    "HUB",
    "SUPORTE",
    "MOCHILA",
    "BATERIA",
]


def canonical_type(value: Any) -> str | None:
    key = DataNormalizer.clean_key(value)
    if not key:
        return None
    if any(token in key for token in ("NOTEBOOK", "NOTBOOK", "NTBOOK", "PERFORMANCE", "PERFOMANCE")):
        return "NOTEBOOK"
    if "MONITOR" in key:
        return "MONITOR"
    if any(token in key for token in ("DESKTOP", "MINIDESK", "MINIDESKTOP", "MINIPC")):
        return "DESKTOP"
    if "HEADSET" in key:
        return "HEADSET"
    if "DOCK" in key:
        return "DOCK"
    if any(token in key for token in ("FONTE", "CARREGADOR", "ADAPTADOR")):
        return "FONTE"
    if "MOUSE" in key:
        return "MOUSE"
    if "TECLADO" in key:
        return "TECLADO"
    if "WEBCAM" in key:
        return "WEBCAM"
    if "HUB" in key:
        return "HUB"
    if "SUPORTE" in key:
        return "SUPORTE"
    if any(token in key for token in ("MALETA", "MOCHILA")):
        return "MOCHILA"
    if "BATERIA" in key:
        return "BATERIA"
    return None


def canonical_brand(value: Any) -> str | None:
    key = DataNormalizer.clean_key(value)
    if not key:
        return None
    if "DELL" in key or key == "DEL":
        return "DELL"
    if any(token in key for token in ("LENOVO", "LENNOVO", "THINKPAD", "THINKCENTRE", "M80Q")):
        return "LENOVO"
    if key == "HP":
        return "HP"
    if "SAMSUNG" in key:
        return "SAMSUNG"
    if "PLANTRONICS" in key or "POLY" in key:
        return "PLANTRONICS"
    if key == "LG":
        return "LG"
    if "KENSINGTON" in key:
        return "KENSINGTON"
    if key == "AOC":
        return "AOC"
    return None


def classify_asset(tipo: Any, serial: Any = None, marca: Any = None, modelo: Any = None) -> str:
    canonical = canonical_type(tipo)
    brand_key = DataNormalizer.clean_key(marca)
    model_key = DataNormalizer.clean_key(modelo)
    if any(token in f"{brand_key} {model_key}" for token in ("PLANTRONICS", "POLY", "C3220", "HEADSET")):
        return "PERIFERICO"
    if any(token in f"{brand_key} {model_key}" for token in ("FONTE", "CARREGADOR", "ADAPTADOR", "LA65", "ADLX", "65W")):
        return "PERIFERICO"
    if canonical in PRINCIPAL_TYPES:
        return "PRINCIPAL"
    if canonical in PERIPHERAL_TYPES:
        return "PERIFERICO"
    return "DIVERSO"
