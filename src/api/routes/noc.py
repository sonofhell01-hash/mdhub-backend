"""Central NOC por equipe: `/noc/me`, `/noc/overview`, `/noc/teams`.

Todos os endpoints exigem sessao valida do HUB (`get_current_user`). A
filtragem por equipe acontece inteiramente no backend
(`src/services/noc/overview.py`) - o cliente pode pedir um `team_id`, mas
so recebe dados se esse `team_id` estiver entre as equipes autorizadas do
usuario autenticado; caso contrario, 403 explicito (nunca uma resposta
vazia, que poderia ser confundida com "equipe sem dados").
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.core.auth import get_current_user
from src.core.db_session import get_db
from src.models.core import User
from src.services.noc.overview import NocAccessError, build_me_payload, build_overview, build_teams_payload


router = APIRouter(prefix="/noc", tags=["Central NOC"])


@router.get("/me")
def noc_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return build_me_payload(current_user, db)


@router.get("/teams")
def noc_teams(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return build_teams_payload(current_user, db)


@router.get("/overview")
def noc_overview(
    team_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return build_overview(current_user, team_id, db)
    except NocAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
