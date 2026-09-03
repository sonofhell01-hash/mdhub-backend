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


@router.get("/stats")
def database_stats(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Tamanho atual do banco e das maiores tabelas (somente leitura).

    Usado pra acompanhar o consumo em relacao ao limite de 0.5 GB do plano
    Free do Neon - nao altera nada, so consulta `pg_database_size` e
    `pg_total_relation_size` (que ja inclui indices/toast de cada tabela).
    So funciona em Postgres (usa funcoes de catalogo do Postgres).
    """
    _require_admin_access(request, authorization, token)
    try:
        database_size = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
        rows = db.execute(
            text(
                """
                SELECT relname AS tabela,
                       pg_total_relation_size(relid) AS bytes
                FROM pg_catalog.pg_statio_user_tables
                ORDER BY bytes DESC
                LIMIT 15
                """
            )
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - nunca vazar traceback bruto pro cliente
        raise HTTPException(status_code=500, detail=f"Falha ao consultar tamanho do banco: {exc}") from exc

    def _fmt_mb(value: int) -> float:
        return round(value / (1024 * 1024), 2)

    limit_bytes = 500 * 1024 * 1024  # 0.5 GB do plano Free do Neon
    return {
        "status": "ok",
        "database_size_bytes": database_size,
        "database_size_mb": _fmt_mb(database_size),
        "neon_free_limit_mb": 500,
        "percent_of_free_limit": round((database_size / limit_bytes) * 100, 1),
        "tables": [
            {"tabela": row.tabela, "bytes": row.bytes, "mb": _fmt_mb(row.bytes)}
            for row in rows
        ],
    }


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


@router.post("/stamp")
def stamp_database(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    revision: str = Query(...),
):
    """Marca o banco como estando em `revision` sem executar nenhum DDL.

    Uso pontual de bootstrap: bancos que tiveram o schema criado via
    `POST /database/init` (create_all direto) nunca tiveram a tabela
    `alembic_version` gravada, entao `command.upgrade` tenta rodar a
    migration 0001 do zero e falha porque as tabelas ja existem. Este
    endpoint carimba a revisao atual (ex.: a ultima migration que ja
    corresponde ao schema existente) para que `POST /database/migrate`
    passe a aplicar so as migrations realmente pendentes dali em diante.
    """
    _require_admin_access(request, authorization, token)
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    try:
        command.stamp(cfg, revision)
    except CommandError as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao carimbar revisao: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - nunca vazar traceback bruto pro cliente
        raise HTTPException(status_code=500, detail=f"Erro inesperado ao carimbar revisao: {exc}") from exc
    return {"status": "ok", "message": f"Banco carimbado na revisao {revision}."}


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
