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
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.core import CheckerState, Document, MidiaSimplesSessionCache, SyncPending, Team, User, UserTeam

_SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")


def _today_utc_range() -> tuple[datetime, datetime]:
    """Janela [inicio, fim) do "hoje" em America/Sao_Paulo, convertida para
    UTC naive (mesmo formato de `documentos.created_at` no banco).

    A Central NOC mostra o total de HOJE por equipe como numero principal
    dos cards (pedido explicito do usuario - um total acumulado desde
    sempre nao serve pra acompanhar operacao em tempo real).
    """
    now_local = datetime.now(_SAO_PAULO_TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


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

# Mapa de tipo de documento -> codigo de evento usado em `sync_pendente.tipo`
# (ver src/services/sync_queue.py). Only os modulos ja sincronizados via fila
# central tem um codigo aqui; os demais (rollout, fechamento, sub/headset)
# nao passam pela fila de sync do MidiaSimples.
_SYNC_EVENT_BY_TIPO = {
    "rat": "RAT_CREATED",
    "laudo": "LAUDO_CREATED",
    "devolucao": "DEVOLUCAO_CREATED",
    "concessao": "CONCESSAO_CREATED",
    "substituicao": "SUBSTITUICAO_CREATED",
    "substituicao_headset": "SUBSTITUICAO_HEADSET_CREATED",
    "emprestimo": "EMPRESTIMO_CREATED",
    "rollout": "ROLLOUT_CREATED",
    "fechamento": "FECHAMENTO_CREATED",
}

# Checker "ok" mais velho que isso e considerado desatualizado (`stale`), nao
# `synced` - mesmo sem erro explicito.
_STALE_AFTER = timedelta(hours=24)


def _iso_utc(value: datetime | None) -> str | None:
    """Serializa um datetime como ISO-8601 SEMPRE com offset UTC explicito.

    As colunas de data no banco (`checked_at`, `created_at`, `enviado_em`)
    sao gravadas como UTC "naive" (sem tzinfo). `datetime.isoformat()` num
    valor naive nao inclui `Z`/offset, e o browser interpreta essa string
    como HORARIO LOCAL - resultando num horario exibido ~3h adiantado para
    quem esta em America/Sao_Paulo. Sempre anexar tzinfo=utc antes de
    serializar evita esse bug (a UI converte para local corretamente).
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


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


def _build_module(db: Session, module_key: str, team_user_ids: list[int]) -> dict[str, Any]:
    """Agrega um modulo NOC a partir de `documentos`/`sync_pendente`/`checker_states`.

    Todos os modulos usam a mesma tabela `documentos` (`Document.tipo`), entao
    a mesma logica de contagem serve para os 9 modulos - a diferenca entre
    eles e so o rotulo, o `checker_modulo` (quando a fonte e MidiaSimples) e
    o codigo de evento de fila associado.

    Um modulo so aparece como `not_synced` quando NENHUM documento desse tipo
    existe ainda para a equipe (nunca "0" quando ha, de fato, zero
    documentos sincronizados vs. zero porque o modulo nunca rodou).
    """
    meta = NOC_MODULES[module_key]
    checker = _checker_state_row(db, meta["checker_modulo"])
    sync_event = _SYNC_EVENT_BY_TIPO.get(module_key)

    total_all_time = 0
    total_today = 0
    pending = 0
    failed = 0
    if team_user_ids:
        base_query = db.query(Document).filter(
            Document.tipo == meta["tipo_documento"], Document.usuario_id.in_(team_user_ids)
        )
        total_all_time = base_query.count()
        today_start, today_end = _today_utc_range()
        total_today = base_query.filter(
            Document.created_at >= today_start, Document.created_at < today_end
        ).count()
        if sync_event:
            pending = (
                db.query(SyncPending)
                .filter(
                    SyncPending.tipo == sync_event,
                    SyncPending.status == "pendente",
                    SyncPending.usuario_id.in_(team_user_ids),
                )
                .count()
            )
            failed = (
                db.query(SyncPending)
                .filter(
                    SyncPending.tipo == sync_event,
                    SyncPending.status == "erro",
                    SyncPending.usuario_id.in_(team_user_ids),
                )
                .count()
            )

    if checker is not None:
        state = _module_state_from_checker(checker)
    elif total_all_time > 0:
        # Modulo sem checker externo (interno do HUB, ex.: rollout/fechamento)
        # mas com documentos reais da equipe: considerar sincronizado, ja que
        # a "fonte" e o proprio HUB.
        state = "synced"
    else:
        state = "not_synced"

    # `total` = HOJE (America/Sao_Paulo) - numero principal do card, pedido
    # explicito do usuario pra acompanhar operacao em tempo real (um total
    # acumulado desde sempre nao serve pra isso). So fica `None` quando o
    # modulo nunca teve NENHUM documento da equipe (`not_synced` de verdade);
    # havendo historico mas nada hoje, mostra "0" mesmo (equipe ativa, dia
    # parado). `total_all_time` fica disponivel como numero secundario.
    has_any_data = checker is not None or total_all_time > 0
    return {
        "label": meta["label"],
        "total": total_today if has_any_data else None,
        "total_all_time": total_all_time if has_any_data else None,
        "state": state,
        # O checker do MidiaSimples e global (a fonte nao recorta por equipe);
        # deixamos isso explicito para a UI nao interpretar como "da equipe".
        "checker_scope": "global" if meta["checker_modulo"] else "n/a",
        "last_synced_at": _iso_utc(checker.checked_at) if checker and checker.status == "ok" else None,
        "pending": pending if sync_event else None,
        "failed": failed if sync_event else None,
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
            "modules": {key: _build_module(db, key, []) for key in NOC_MODULES},
            "alerts": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    team = db.get(Team, team_id)
    team_user_ids = _team_active_user_ids(db, team_id)
    technicians = _team_active_technicians(db, team_id)

    modules = {key: _build_module(db, key, team_user_ids) for key in NOC_MODULES}
    alerts = _build_module_alerts(modules)

    return {
        "team": {"id": team.id, "name": team.nome, "code": team.codigo} if team else None,
        "scope": {"role": scope.role, "can_switch_teams": scope.can_switch_teams},
        "technicians": {"active": len(technicians), "items": technicians},
        "modules": modules,
        "alerts": alerts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_module_alerts(modules: dict[str, Any]) -> list[dict[str, Any]]:
    """Alertas curtos derivados diretamente dos modulos ja calculados, para
    exibir no proprio card do `/noc/overview` (o `/noc/alerts` traz a visao
    completa, incluindo sessao MidiaSimples e documentos sem atribuicao)."""
    alerts: list[dict[str, Any]] = []
    for key, module in modules.items():
        if module["state"] == "error":
            alerts.append({"type": "checker_error", "module": key, "message": f"Checker de {module['label']} com erro."})
        if (module["failed"] or 0) > 0:
            alerts.append(
                {
                    "type": "sync_failed",
                    "module": key,
                    "message": f"{module['failed']} envio(s) de {module['label']} com falha na fila.",
                }
            )
    return alerts


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


def _document_summary(document: Document) -> dict[str, Any]:
    """Serializa um documento para `/noc/documents` SEM `payload`/`response_payload`
    brutos - eles podem carregar dados pessoais e HTML cru da fonte."""
    return {
        "id": document.id,
        "tipo": document.tipo,
        "numero_chamado": document.numero_chamado,
        "midiasimples_id": document.midiasimples_id,
        "status": document.status,
        "usuario_id": document.usuario_id,
        "responsavel": (document.user.apelido or document.user.nome) if document.user else None,
        "sync_pendente": document.sync_pendente,
        "created_at": _iso_utc(document.created_at),
        "enviado_em": _iso_utc(document.enviado_em),
    }


def build_documents_payload(
    user: User,
    requested_team_id: int | None,
    db: Session,
    *,
    tipo: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """Lista paginada de documentos da equipe autorizada do usuario.

    Nunca devolve `payload`/`response_payload` brutos (ver `_document_summary`).
    `team_id` fora do escopo autorizado sempre levanta `NocAccessError`.
    """
    scope = resolve_scope(user, db)
    team_id = resolve_target_team(scope, requested_team_id)

    if team_id is None:
        return {"items": [], "page": page, "page_size": page_size, "total": 0}

    team_user_ids = _team_active_user_ids(db, team_id)
    if not team_user_ids:
        return {"items": [], "page": page, "page_size": page_size, "total": 0}

    query = db.query(Document).filter(Document.usuario_id.in_(team_user_ids))
    if tipo:
        query = query.filter(Document.tipo == tipo)
    if status:
        query = query.filter(Document.status == status)

    total = query.count()
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    items = (
        query.order_by(Document.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "items": [_document_summary(item) for item in items],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def _midiasimples_session_alerts(db: Session, emails: list[str]) -> list[dict[str, Any]]:
    if not emails:
        return []
    normalized = [email.strip().lower() for email in emails]
    now = datetime.now(timezone.utc)
    sessions = (
        db.query(MidiaSimplesSessionCache)
        .filter(func.lower(MidiaSimplesSessionCache.email).in_(normalized))
        .all()
    )
    alerts: list[dict[str, Any]] = []
    for session in sessions:
        expires_at = session.expires_at
        expired = False
        if session.status != "ativa":
            expired = True
        elif expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            expired = expires_at <= now
        if expired:
            alerts.append(
                {
                    "type": "midiasimples_session_expired",
                    "email": session.email,
                    "message": f"Sessao MidiaSimples de {session.user_name or session.email} expirada ou invalida.",
                }
            )
    return alerts


def build_alerts_payload(user: User, requested_team_id: int | None, db: Session) -> dict[str, Any]:
    """Alertas agregados e escopados pela equipe autorizada do usuario:

    - checker com erro / fonte desatualizada (globais, a fonte nao recorta
      por equipe - mas so aparecem se a equipe tiver algum modulo dependente
      sincronizado);
    - fila `sync_pendente` com falha, por modulo, contada so para a equipe;
    - sessao MidiaSimples expirada, restrita aos tecnicos da propria equipe;
    - documentos sem atribuicao (`usuario_id is NULL`), visivel somente a
      `admin`/`gestor_noc` (evita expor esse numero, que pode ser lido como
      falha de outra equipe, para um tecnico comum).
    """
    scope = resolve_scope(user, db)
    team_id = resolve_target_team(scope, requested_team_id)

    if team_id is None:
        return {"items": [], "generated_at": datetime.now(timezone.utc).isoformat()}

    team_user_ids = _team_active_user_ids(db, team_id)
    technicians = _team_active_technicians(db, team_id)

    alerts: list[dict[str, Any]] = []

    for key, meta in NOC_MODULES.items():
        checker = _checker_state_row(db, meta["checker_modulo"])
        if checker is None:
            continue
        if checker.status == "erro":
            alerts.append(
                {
                    "type": "checker_error",
                    "module": key,
                    "message": f"Checker de {meta['label']} com erro: {checker.ultimo_erro or 'sem detalhes'}.",
                }
            )
        elif _module_state_from_checker(checker) == "stale":
            alerts.append(
                {
                    "type": "checker_stale",
                    "module": key,
                    "message": f"Checker de {meta['label']} desatualizado (mais de {int(_STALE_AFTER.total_seconds() // 3600)}h sem sincronizar).",
                }
            )

        sync_event = _SYNC_EVENT_BY_TIPO.get(key)
        if sync_event and team_user_ids:
            failed = (
                db.query(SyncPending)
                .filter(
                    SyncPending.tipo == sync_event,
                    SyncPending.status == "erro",
                    SyncPending.usuario_id.in_(team_user_ids),
                )
                .count()
            )
            if failed:
                alerts.append(
                    {
                        "type": "sync_failed",
                        "module": key,
                        "message": f"{failed} envio(s) de {meta['label']} com falha na fila de sincronizacao.",
                    }
                )

    team_emails = [tech["email"] for tech in technicians if tech.get("email")]
    alerts.extend(_midiasimples_session_alerts(db, team_emails))

    if scope.role in {"admin", "gestor_noc"}:
        unassigned = db.query(Document).filter(Document.usuario_id.is_(None)).count()
        if unassigned:
            alerts.append(
                {
                    "type": "unassigned_documents",
                    "module": None,
                    "message": f"{unassigned} documento(s) sem responsavel identificado (grupo Sem equipe).",
                }
            )

    return {"items": alerts, "generated_at": datetime.now(timezone.utc).isoformat()}
