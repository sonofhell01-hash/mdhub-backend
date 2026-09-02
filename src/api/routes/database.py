from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.routes.admin import _require_admin_access
from src.core.db_session import create_database_schema, get_db
from src.models.core import User
from src.services.technicians import list_technicians


router = APIRouter(prefix="/database", tags=["Banco Central"])


@router.get("/status")
def database_status(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/init")
def init_database(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    _require_admin_access(request, authorization, token)
    create_database_schema()
    return {"status": "ok", "message": "Schema central criado/atualizado."}


@router.post("/seed/tecnicos")
def seed_technicians(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    _require_admin_access(request, authorization, token)
    created = 0
    updated = 0
    for technician in list_technicians():
        user = db.query(User).filter(User.email == technician.email).first()
        if user:
            user.nome = technician.full_name
            user.apelido = technician.display_name
            user.midiasimples_id = technician.midiasimples_id
            user.perfil = "tecnico"
            user.ativo = technician.active
            updated += 1
            continue
        db.add(
            User(
                nome=technician.full_name,
                apelido=technician.display_name,
                email=technician.email,
                midiasimples_id=technician.midiasimples_id,
                perfil="tecnico",
                ativo=technician.active,
            )
        )
        created += 1
    db.commit()
    return {"status": "ok", "created": created, "updated": updated}
