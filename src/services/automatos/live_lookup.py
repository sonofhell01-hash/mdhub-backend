from __future__ import annotations

import threading
import time
from typing import Any

from src.services.automatos.client import AutomatosApiError, AutomatosClient
from src.services.automatos.snapshot_store import _snapshot_row
from src.services.inventory.normalizer import DataNormalizer


# Cache em memoria por instancia (processo/lambda quente). Evita bater na API
# do Automatos a cada tecla digitada, sem depender de nenhum arquivo local
# (que nao sobrevive entre execucoes em ambiente serverless).
_CACHE_TTL_SECONDS = 180
_cache_lock = threading.Lock()
# fetched_at comeca em -inf (nao 0.0): time.monotonic() e relativo ao boot do
# processo/container, entao num container serverless recem-criado o relogio
# pode estar bem abaixo de 180s, e "now - 0.0 < 180" ficaria falsamente True,
# fazendo a primeira chamada devolver o cache vazio inicial em vez de buscar
# de verdade na API do Automatos.
_cache: dict[str, Any] = {"rows": [], "fetched_at": float("-inf"), "error": None}


def _fetch_and_normalize() -> list[dict[str, Any]]:
    client = AutomatosClient()
    if not client.configured:
        raise AutomatosApiError("AUTOMATOS_ID/AUTOMATOS_SECURITY_KEY nao configurados.")
    snapshot = client.get_desktops()
    synced_at = str(int(time.time()))
    rows: list[dict[str, Any]] = []
    for raw in snapshot.rows:
        if not isinstance(raw, dict):
            continue
        item = _snapshot_row(raw, synced_at)
        if any((item["machine_id"], item["serial"], item["computer_name_key"])):
            rows.append(item)
    return rows


def get_snapshot_rows(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Retorna as linhas normalizadas do Automatos, buscando ao vivo com cache curto.

    Substitui a leitura da antiga tabela SQLite `automatos_snapshot`, que era
    populada por um job de sincronizacao periodico e nao tem onde persistir
    em ambiente serverless.
    """
    now = time.monotonic()
    with _cache_lock:
        fresh = now - _cache["fetched_at"] < _CACHE_TTL_SECONDS
        if fresh and not force_refresh:
            return _cache["rows"]

    try:
        rows = _fetch_and_normalize()
    except AutomatosApiError:
        with _cache_lock:
            # Mantem o cache anterior (se houver) em vez de derrubar a busca.
            if _cache["rows"]:
                return _cache["rows"]
        raise

    with _cache_lock:
        _cache["rows"] = rows
        _cache["fetched_at"] = now
        _cache["error"] = None
    return rows


def find_automatos(
    serial: str | None = None,
    patrimonio: str | None = None,
    hostname: str | None = None,
    matricula_key: str | None = None,
) -> dict[str, Any] | None:
    if not any((serial, patrimonio, hostname, matricula_key)):
        return None
    try:
        rows = get_snapshot_rows()
    except AutomatosApiError:
        return None

    matches = []
    for row in rows:
        if serial and row.get("serial") == serial:
            matches.append(row)
            continue
        if patrimonio and row.get("patrimonio") == patrimonio:
            matches.append(row)
            continue
        if hostname and row.get("computer_name_key") == DataNormalizer.clean_key(hostname):
            matches.append(row)
            continue
        if matricula_key and row.get("top_user_key") == matricula_key:
            matches.append(row)
            continue
    if not matches:
        return None
    matches.sort(key=lambda item: (item.get("collect_date") or "", item.get("update_date") or ""), reverse=True)
    best = dict(matches[0])
    best.pop("raw_payload", None)
    return {key: value for key, value in best.items() if value not in (None, "")}
