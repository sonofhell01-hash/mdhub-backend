from typing import Any

from fastapi import APIRouter, Query

from src.core.database import connect, shared_db_path
from src.services.prefill.operational_prefill import OperationalPrefillService


router = APIRouter(prefix="/operacional", tags=["Consulta Operacional"])


@router.get("/status")
def operational_status() -> dict[str, Any]:
    db_path = shared_db_path()
    if not db_path.exists():
        return {
            "status": "missing_db",
            "database_path": str(db_path),
            "tables": [],
        }

    with connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()

    return {
        "status": "ok",
        "database_path": str(db_path),
        "tables": [row["name"] for row in rows],
    }


@router.get("/debug/automatos")
def debug_automatos_live(q: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Rota temporaria de diagnostico: isola a busca ao vivo do Automatos, sem
    passar por AssetLookup/RatContextService, pra depurar por que uma busca
    que deveria bater nao esta encontrando nada."""
    from src.services.automatos.live_lookup import get_snapshot_rows, find_automatos
    from src.services.inventory.normalizer import DataNormalizer

    serial = DataNormalizer.normalize_serial(q)
    patrimonio = DataNormalizer.normalize_patrimonio(q)
    hostname = DataNormalizer.normalize_hostname(q)
    matricula_key = DataNormalizer.matricula_key(q)

    error = None
    rows: list[Any] = []
    try:
        rows = get_snapshot_rows()
    except Exception as exc:
        error = f"get_snapshot_rows: {type(exc).__name__}: {exc}"

    match = None
    try:
        match = find_automatos(serial, patrimonio, hostname, matricula_key)
    except Exception as exc:
        error = f"{error or ''} | find_automatos: {type(exc).__name__}: {exc}"

    return {
        "query": q,
        "normalized": {
            "serial": serial,
            "patrimonio": patrimonio,
            "hostname": hostname,
            "matricula_key": matricula_key,
        },
        "row_count": len(rows),
        "error": error,
        "match": match,
        "sample_keys": [
            {"computer_name_key": r.get("computer_name_key"), "serial": r.get("serial"), "top_user_key": r.get("top_user_key")}
            for r in rows[:5]
        ],
    }


@router.get("/search")
def operational_search(
    q: str = Query(..., min_length=2, description="Matricula, serial, hostname, nome ou e-mail"),
) -> dict[str, Any]:
    # O banco local legado (SQLite sincronizado) e opcional: em ambiente serverless
    # ele normalmente nao existe/nao persiste entre execucoes. As fontes vivas
    # (Automatos ao vivo + MidiaSimples) continuam funcionando sem ele, entao a
    # consulta nao deve falhar so por causa dessa base auxiliar estar ausente.
    service = OperationalPrefillService()
    result = service.build_for_rat(q)
    result["summary"] = service.format_summary(result)
    return result
