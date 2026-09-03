"""Resolucao de `usuario_id` para documentos sincronizados de fontes externas
(MidiaSimples) e para o backfill de registros historicos.

Ordem definida no doc de handoff (README_IMPLEMENTACAO_NOC_POR_EQUIPES.md,
secao "Atribuicao de documentos"):

1. `arklok_user_id`, `responsible_id`, `midiasimples_id` do tecnico (ou
   equivalente) presente na linha sincronizada;
2. e-mail do responsavel, se a resposta fornecer;
3. nome normalizado de `arklok_responsible`/`responsible`/`technician_name`
   (ou equivalentes) - so aceita quando ha exatamente UM usuario ativo com
   aquele nome normalizado; nome ambiguo NUNCA e atribuido por semelhanca;
4. se nada acima resolver com seguranca, `usuario_id = None` (classificado
   como `sem_equipe` para fins de agregacao/alertas).

Este modulo nunca atribui por "equipe de quem rodou o checker" nem por
semelhanca fraca de nome - isso distorceria os numeros da Central NOC.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.core import User


_RESPONSIBLE_ID_KEYS = (
    "arklok_user_id",
    "responsible_id",
    "arklok_responsible_id",
    "technician_midiasimples_id",
    "technician_id",
)
_RESPONSIBLE_EMAIL_KEYS = (
    "responsible_email",
    "arklok_responsible_email",
    "technician_email",
    "analyst_email",
    "email_responsavel",
    "email",
)
_RESPONSIBLE_NAME_KEYS = (
    "arklok_responsible",
    "responsible",
    "technician_name",
    "responsavel_arklok",
    "responsavel",
    "analyst",
    "analista",
    "technician",
    "tecnico",
)

ResolutionMethod = str  # "id" | "email" | "nome" | "ambiguo" | "nao_encontrado"


@dataclass
class ResolutionResult:
    usuario_id: int | None
    method: ResolutionMethod
    matched_name: str | None = None


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_name(value: str) -> str:
    """Remove acentos/pontuacao e uppercasa, para comparacao de nomes."""
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip().upper()


def resolve_document_owner(
    db: Session,
    row: dict[str, Any],
    *,
    fallback_name: str | None = None,
) -> ResolutionResult:
    """Resolve o `usuario_id` responsavel por um documento sincronizado.

    `row` e o dicionario bruto da fonte externa (ex.: linha do MidiaSimples).
    `fallback_name` e usado somente se `row` nao trouxer nenhuma das chaves
    de nome conhecidas (ex.: nome ja extraido por um parser especifico do
    modulo, como `_rat_responsible`).
    """
    raw_id = _pick(row, *_RESPONSIBLE_ID_KEYS)
    if raw_id not in (None, ""):
        try:
            midi_id = int(raw_id)
        except (TypeError, ValueError):
            midi_id = None
        if midi_id is not None:
            user = db.query(User).filter(User.midiasimples_id == midi_id, User.ativo.is_(True)).first()
            if user:
                return ResolutionResult(user.id, "id", user.nome)

    email = _pick(row, *_RESPONSIBLE_EMAIL_KEYS)
    if email:
        normalized_email = str(email).strip().lower()
        user = db.query(User).filter(func.lower(User.email) == normalized_email, User.ativo.is_(True)).first()
        if user:
            return ResolutionResult(user.id, "email", user.nome)

    name = _pick(row, *_RESPONSIBLE_NAME_KEYS) or fallback_name
    if name:
        normalized = normalize_name(str(name))
        if normalized:
            active_users = db.query(User).filter(User.ativo.is_(True)).all()
            candidates = [
                user
                for user in active_users
                if normalize_name(user.nome) == normalized
                or (user.apelido and normalize_name(user.apelido) == normalized)
            ]
            if len(candidates) == 1:
                return ResolutionResult(candidates[0].id, "nome", candidates[0].nome)
            if len(candidates) > 1:
                return ResolutionResult(None, "ambiguo", None)

    return ResolutionResult(None, "nao_encontrado", None)
