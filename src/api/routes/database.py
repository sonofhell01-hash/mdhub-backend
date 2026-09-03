from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.routes.admin import _require_admin_access
from src.core.db_session import create_database_schema, get_db
from src.models.core import User
from src.services.noc_seed import seed_noc_teams
from src.services.technicians import list_technicians


router = APIRouter(prefix="/database", tags=["Banco Central"])

# Raiz do repo calculada a partir deste arquivo (nao do cwd), porque em
# serverless (Vercel) o diretorio de trabalho na hora da chamada nao e
# garantido ser a raiz do projeto.
_REPO_ROOT = Path(__file__).resolve().parents[3]


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


@router.post("/migrate")
def migrate_database(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    """Aplica as migrations Alembic pendentes ate a revisao mais recente.

    Roda via API do Alembic (nao subprocess/shell), porque o ambiente
    serverless (Vercel) nao garante shell persistente. A conexao usada e
    a mesma engine viva da aplicacao (ver migrations/env.py), entao aponta
    automaticamente para o DATABASE_URL configurado no ambiente atual.
    """
    _require_admin_access(request, authorization, token)
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    try:
        command.upgrade(cfg, "head")
    except CommandError as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao aplicar migrations: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - nunca vazar traceback bruto pro cliente
        raise HTTPException(status_code=500, detail=f"Erro inesperado ao aplicar migrations: {exc}") from exc
    return {"status": "ok", "message": "Migrations aplicadas ate a revisao mais recente."}


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


@router.post("/seed/noc")
def seed_noc(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Cria/atualiza as equipes, usuarios e vinculos da Central NOC.

    Idempotente: pode ser chamado quantas vezes for preciso sem duplicar
    equipes, usuarios ou vinculos (upsert por codigo/email). Nunca apaga
    usuarios existentes. Requer que a migration 20260903_0008_noc_teams ja
    tenha sido aplicada (ver POST /database/migrate).
    """
    _require_admin_access(request, authorization, token)
    try:
        stats = seed_noc_teams(db)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - nunca vazar traceback bruto pro cliente
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Falha ao rodar seed NOC: {exc}") from exc
    return {"status": "ok", **stats}
