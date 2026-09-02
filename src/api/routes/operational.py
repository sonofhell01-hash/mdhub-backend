from typing import Any

from fastapi import APIRouter, HTTPException, Query

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


@router.get("/search")
def operational_search(
    q: str = Query(..., min_length=2, description="Matricula, serial, hostname, nome ou e-mail"),
) -> dict[str, Any]:
    db_path = shared_db_path()
    if not db_path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Banco local nao encontrado: {db_path}",
        )

    service = OperationalPrefillService()
    result = service.build_for_rat(q)
    result["summary"] = service.format_summary(result)
    return result
