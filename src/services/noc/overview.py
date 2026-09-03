"""Servico de agregacao e autorizacao da Central NOC por equipe.

Mantem a consulta/autorizacao/agregacao fora das rotas (`src/api/routes/noc.py`).

Regra central de seguranca: toda a filtragem por equipe acontece aqui, no
backend, a partir do usuario autenticado (JWT -> `usuarios`/`usuario_equipes`
lidos frescos do banco a cada chamada). O cliente nunca decide sozinho qual
equipe ve; um `team_id` fora do escopo autorizado sempre vira 403.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from src.models.core import CheckerState, Document, SyncPending, Team, User, UserTeam


# Rotulos e ordem de exibicao dos modulos operacionais da Central NOC.
# `tipo_documento` casa com `Document.tipo`; `checker_modulo` casa com
# `CheckerState.modulo` (fonte "midiasimples") quando o modulo tem checker
# externo. Modulos sem checker (internos do HUB) ficam com `checker_modulo=None`.
NOC_MODULES: dict[str, dict[str, str | None]] = {
    "rat": {"label": "Atendimentos", "tipo_documento": "rat", "checker_modulo": "rats"},
    "laudo": {"label": "Laudos", "tipo_documento": "laudo", "checker_modulo": "laudos"},
    "concessao": {"label": "Termos de concessão", "tipo_documento": "concessao", "checker_modulo": "concessoes"},
    "devolucao": {"label": "Termos de devolução", "tipo_documento": "devolucao", "checker_modulo": "devolucoes"},
    "emprestimo": {"label": "Empréstimos", "tipo_documento": "emprestimo", "checker_modulo": "emprestimos"},
    "substituicao": {"label": "Substituições", "tipo_documento": "substituicao", "checker_modulo": None},
    "substituicao_headset": {"label": "Sub/headset", "tipo_documento": "substituicao_headset", "checker_modulo": None},
    "rollout": {"label": "Rollout", "tipo_documento": "rollout", "checker_modulo": None},
    "fechamento": {"label": "Fechamento", "tipo_documento": "fechamento", "checker_modulo": None},
}

# Modulos ja agregados com dados reais (Etapa 3). Os demais aparecem como
# `not_synced` ate a Etapa 4 completar a agregacao de cada um.
_IMPLEMENTED_MODULES = {"rat"}

# Mapa de tipo de documento -> codigo de evento usado em `sync_pendente.tipo`
# (ver src/services/sync_queue.py). Only os modulos ja sincronizados via fila
# central tem um codigo aqui.
_SYNC_EVENT_BY_TIPO = {
    "rat": "RAT_CREATED",
    "laudo": "LAUDO_CREATED",
    "devolucao": "DEVOLUCAO_CREATED",
    "concessao": "CONCESSAO_CREATED",
    "substituicao": "SUBSTITUICAO_CREATED",
    "emprestimo": "EMPRESTIMO_CREATED",
}

# Checker "ok" mais velho que isso e considerado desatualizado (`stale`), nao
# `synced` - mesmo sem erro explicito.
_STALE_AFTER = timedelta(hours=24)


class NocAccessError(Exception):
    """Levantado quando o usuario pede uma equipe fora do proprio escopo."""


@dataclass
class NocScope:
    role: str
    team_ids: list[int]
    default_team_id: int | None
    can_switch_teams: bool


def resolve_scope(user: User, db: Session) -> NocScope:
    """Resolve o escopo de equipes do usuario autenticado, lido do banco.

    - `tecnico`/`admin`: apenas as equipes com vinculo ativo em `usuario_equipes`.
    - `gestor_noc`: todas as equipes ativas (visao ampla, ver doc de handoff).

    Nunca confia em nada vindo do token alem de `sub`/`email` - o papel e os
    vinculos sao sempre lidos frescos aqui.
    """
    role = user.perfil_noc or "tecnico"

    links = (
        db.query(UserTeam)
        .join(Team, Team.id == UserTeam.equipe_id)
        .filter(UserTeam.usuario_id == user.id, UserTeam.ativa.is_(True), Team.ativa.is_(True))
        .all()
    )
    linked_team_ids = [link.equipe_id for link in links]
    principal_link = next((link for link in links if link.principal), None)

    if role == "gestor_noc":
        all_teams = db.query(Team.id).filter(Team.ativa.is_(True)).order_by(Team.nome.asc()).all()
        team_ids = [row[0] for row in all_teams]
        default_team_id = (
            principal_link.equipe_id
            if principal_link
            else (linked_team_ids[0] if linked_team_ids else (team_ids[0] if team_ids else None))
        )
    else:
        team_ids = linked_team_ids
        default_team_id = principal_link.equipe_id if principal_link else (team_ids[0] if team_ids else None)

    return NocScope(
        role=role,
        team_ids=team_ids,
        default_team_id=default_team_id,
        can_switch_teams=len(team_ids) > 1,
    )


def resolve_target_team(scope: NocScope, requested_team_id: int | None) -> int | None:
    """Decide qual equipe usar para uma chamada: a pedida (se autorizada) ou
    a principal do usuario. Levanta `NocAccessError` se `requested_team_id`
    estiver fora do escopo autorizado - nunca retorna dados de outra equipe.
    """
    if requested_team_id is None:
        return scope.default_team_id
    if requested_team_id not in scope.team_ids:
        raise NocAccessError(f"Equipe {requested_team_id} nao autorizada para este usuario.")
    return requested_team_id


def _team_active_user_ids(db: Session, team_id: int) -> list[int]:
    rows = (
        db.query(User.id)
        .join(UserTeam, UserTeam.usuario_id == User.id)
        .filter(UserTeam.equipe_id == team_id, UserTeam.ativa.is_(True), User.ativo.is_(True))
        .all()
    )
    return [row[0] for row in rows]


def _team_active_technicians(db: Session, team_id: int) -> list[dict[str, Any]]:
    users = (
        db.query(User)
        .join(UserTeam, UserTeam.usuario_id == User.id)
        .filter(UserTeam.equipe_id == team_id, UserTeam.ativa.is_(True), User.ativo.is_(True))
        .order_by(User.nome.asc())
        .all()
    )
    return [
        {
            "id": user.id,
            "nome": user.nome,
            "apelido": user.apelido,
            "email": user.email,
            "perfil_noc": user.perfil_noc,
        }
        for user in users
    ]


def _checker_state_row(db: Session, checker_modulo: str | None) -> CheckerState | None:
    if not checker_modulo:
        return None
    return (
        db.query(CheckerState)
        .filter(CheckerState.fonte == "midiasimples", CheckerState.modulo == checker_modulo)
        .first()
    )


def _module_state_from_checker(checker: CheckerState | None) -> str:
    if checker is None:
        return "not_synced"
    if checker.status == "erro":
        return "error"
    if checker.status == "ok":
        checked_at = checker.checked_at
        if checked_at is None:
            return "stale"
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - checked_at > _STALE_AFTER:
            return "stale"
        return "synced"
    return "syncing"


def _build_rat_module(db: Session, team_user_ids: list[int]) -> dict[str, Any]:
    meta = NOC_MODULES["rat"]
    checker = _checker_state_row(db, meta["checker_modulo"])
    state = _module_state_from_checker(checker)

    total = 0
    pending = 0
    failed = 0
    if team_user_ids:
        total = (
            db.query(Document)
            .filter(Document.tipo == "rat", Document.usuario_id.in_(team_user_ids))
            .count()
        )
        pending = (
            db.query(SyncPending)
            .filter(
                SyncPending.tipo == _SYNC_EVENT_BY_TIPO["rat"],
                SyncPending.status == "pendente",
                SyncPending.usuario_id.in_(team_user_ids),
            )
            .count()
        )
        failed = (
            db.query(SyncPending)
            .filter(
                SyncPending.tipo == _SYNC_EVENT_BY_TIPO["rat"],
                SyncPending.status == "erro",
                SyncPending.usuario_id.in_(team_user_ids),
            )
            .count()
        )

    return {
        "label": meta["label"],
        "total": total,
        "state": state,
        # O checker do MidiaSimples e global (a fonte nao recorta por equipe);
        # deixamos isso explicito para a UI nao interpretar como "da equipe".
        "checker_scope": "global",
        "last_synced_at": checker.checked_at.isoformat() if checker and checker.status == "ok" and checker.checked_at else None,
        "pending": pending,
        "failed": failed,
    }


def _placeholder_module(module_key: str) -> dict[str, Any]:
    meta = NOC_MODULES[module_key]
    return {
        "label": meta["label"],
        "total": None,
        "state": "not_synced",
        "checker_scope": "global" if meta["checker_modulo"] else "n/a",
        "last_synced_at": None,
        "pending": None,
        "failed": None,
    }


def build_overview(user: User, requested_team_id: int | None, db: Session) -> dict[str, Any]:
    scope = resolve_scope(user, db)
    team_id = resolve_target_team(scope, requested_team_id)

    if team_id is None:
        # Usuario sem nenhuma equipe ativa vinculada: overview vazio, nao erro -
        # evita expor dados de outras equipes so porque o usuario nao tem uma.
        return {
            "team": None,
            "scope": {"role": scope.role, "can_switch_teams": scope.can_switch_teams},
            "technicians": {"active": 0, "items": []},
            "modules": {key: _placeholder_module(key) for key in NOC_MODULES},
            "alerts": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    team = db.get(Team, team_id)
    team_user_ids = _team_active_user_ids(db, team_id)
    technicians = _team_active_technicians(db, team_id)

    modules: dict[str, Any] = {}
    for key in NOC_MODULES:
        if key in _IMPLEMENTED_MODULES:
            modules[key] = _build_rat_module(db, team_user_ids)
        else:
            modules[key] = _placeholder_module(key)

    return {
        "team": {"id": team.id, "name": team.nome, "code": team.codigo} if team else None,
        "scope": {"role": scope.role, "can_switch_teams": scope.can_switch_teams},
        "technicians": {"active": len(technicians), "items": technicians},
        "modules": modules,
        "alerts": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_me_payload(user: User, db: Session) -> dict[str, Any]:
    scope = resolve_scope(user, db)
    links = (
        db.query(UserTeam, Team)
        .join(Team, Team.id == UserTeam.equipe_id)
        .filter(UserTeam.usuario_id == user.id, UserTeam.ativa.is_(True), Team.ativa.is_(True))
        .order_by(Team.nome.asc())
        .all()
    )
    teams = [
        {"id": team.id, "name": team.nome, "code": team.codigo, "principal": bool(link.principal)}
        for link, team in links
    ]
    if scope.role == "gestor_noc":
        seen_ids = {t["id"] for t in teams}
        extra_teams = (
            db.query(Team)
            .filter(Team.ativa.is_(True), ~Team.id.in_(seen_ids) if seen_ids else Team.ativa.is_(True))
            .order_by(Team.nome.asc())
            .all()
        )
        teams.extend({"id": t.id, "name": t.nome, "code": t.codigo, "principal": False} for t in extra_teams)

    return {
        "user": {
            "id": user.id,
            "name": user.apelido or user.nome,
            "email": user.email,
            "role": scope.role,
        },
        "teams": teams,
        "default_team_id": scope.default_team_id,
        "can_switch_teams": scope.can_switch_teams,
    }


def build_teams_payload(user: User, db: Session) -> dict[str, Any]:
    scope = resolve_scope(user, db)
    if not scope.team_ids:
        return {"items": []}
    teams = db.query(Team).filter(Team.id.in_(scope.team_ids)).order_by(Team.nome.asc()).all()
    return {"items": [{"id": team.id, "name": team.nome, "code": team.codigo} for team in teams]}
