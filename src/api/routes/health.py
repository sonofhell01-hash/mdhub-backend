from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from src.core.config import settings
from src.core.db_session import engine


router = APIRouter(tags=["Sistema"])


@router.get("/health")
def health_check():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "env": settings.app_env,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/live")
def health_live():
    """Liveness: the process is up and answering requests."""
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready():
    """Readiness: the process is up AND the database is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
        ready = True
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"
        ready = False

    return {
        "status": "ok" if ready else "degraded",
        "database": db_status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
