import re
import unicodedata
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.auth import get_current_user
from src.core.db_session import get_db
from src.models.core import OperationalTemplate, User
from src.schemas.templates import OperationalTemplateCreate, OperationalTemplateResponse


router = APIRouter(prefix="/templates", tags=["Templates operacionais"])

LAUDO_FIELDS = {"usoInadequado", "acoes", "defeito", "analise", "solucao", "pecaTrocada", "condicaoReparo"}
RAT_FIELDS = {"outro", "problema", "fechamento", "diagnostico", "causaRaiz", "resultado", "needsSerial"}


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().casefold())
    return re.sub(r"[^a-z0-9]+", "-", "".join(char for char in normalized if not unicodedata.combining(char))).strip("-")


def _clean_payload(document_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = LAUDO_FIELDS if document_type == "laudo" else RAT_FIELDS
    required = ({"usoInadequado", "acoes", "defeito", "analise", "solucao", "pecaTrocada", "condicaoReparo"}
                if document_type == "laudo" else {"outro", "problema", "fechamento"})
    unknown = set(payload) - allowed
    missing = required - set(payload)
    if unknown or missing:
        raise HTTPException(status_code=422, detail={"missing": sorted(missing), "unknown": sorted(unknown)})
    cleaned: dict[str, Any] = {}
    for field, value in payload.items():
        if field in {"usoInadequado", "needsSerial"}:
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail=f"{field} deve ser booleano")
            cleaned[field] = value
            continue
        if not isinstance(value, str):
            raise HTTPException(status_code=422, detail=f"{field} deve ser texto")
        text = value.strip()
        if len(text) > 4000:
            raise HTTPException(status_code=422, detail=f"{field} excede 4000 caracteres")
        cleaned[field] = text
    if document_type == "laudo" and cleaned["condicaoReparo"] not in {"reparavel", "irreparavel"}:
        raise HTTPException(status_code=422, detail="condicaoReparo invalida")
    return cleaned


@router.get("", response_model=list[OperationalTemplateResponse])
def list_templates(
    document_type: Literal["laudo", "rat"] = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del user
    query = (select(OperationalTemplate)
             .where(OperationalTemplate.document_type == document_type, OperationalTemplate.active.is_(True))
             .order_by(OperationalTemplate.category, OperationalTemplate.label))
    return list(db.scalars(query))


@router.post("", response_model=OperationalTemplateResponse, status_code=201)
def create_template(
    body: OperationalTemplateCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    label = body.label.strip()
    label_key = _key(label)
    if len(label_key) < 3:
        raise HTTPException(status_code=422, detail="Nome do template invalido")
    category = body.category.strip().casefold() if body.document_type == "rat" else ""
    if body.document_type == "rat" and not category:
        raise HTTPException(status_code=422, detail="Categoria obrigatoria para template RAT")
    item = OperationalTemplate(
        document_type=body.document_type,
        category=category,
        label=label,
        label_key=label_key,
        payload=_clean_payload(body.document_type, body.payload),
        source="manual",
        active=True,
        created_by_user_id=user.id,
    )
    db.add(item)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ja existe um template com esse nome nesta categoria") from exc
    return item
