from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.core.db_session import get_db
from src.models.core import AssetEvidence
from src.services.inventory.normalizer import DataNormalizer
from src.services.legacy.banco_clientes import buscar_equipamento_por_serial


router = APIRouter(prefix="/evidences", tags=["Evidencias Consolidadas"])


def _serialize(item: AssetEvidence) -> dict:
    return {
        "id": item.id,
        "fonte": item.fonte,
        "modulo": item.modulo,
        "external_id": item.external_id,
        "serial": item.serial,
        "patrimonio": item.patrimonio,
        "hostname": item.hostname,
        "matricula": item.matricula,
        "nome": item.nome,
        "email": item.email,
        "categoria": item.categoria,
        "marca": item.marca,
        "modelo": item.modelo,
        "status": item.status,
        "confidence": item.confidence,
        "evidence_at": item.evidence_at.isoformat() if item.evidence_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.get("/")
def list_evidences(
    q: str | None = Query(None, description="Serial, hostname, patrimonio, matricula, nome ou e-mail"),
    fonte: str | None = Query(None),
    modulo: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(AssetEvidence).order_by(AssetEvidence.evidence_at.desc().nullslast(), AssetEvidence.updated_at.desc())
    if fonte:
        query = query.filter(AssetEvidence.fonte == fonte)
    if modulo:
        query = query.filter(AssetEvidence.modulo == modulo)
    if q:
        key = DataNormalizer.clean_key(q)
        text = f"%{q.strip()}%"
        query = query.filter(
            or_(
                AssetEvidence.serial == DataNormalizer.normalize_serial(q),
                AssetEvidence.hostname == DataNormalizer.normalize_hostname(q),
                AssetEvidence.patrimonio == DataNormalizer.normalize_patrimonio(q),
                AssetEvidence.matricula == DataNormalizer.matricula_key(q),
                AssetEvidence.nome.ilike(text),
                AssetEvidence.email.ilike(text),
                AssetEvidence.external_id == key,
            )
        )
    items = query.limit(limit).all()
    return {"items": [_serialize(item) for item in items]}


@router.get("/asset-by-serial/{serial}")
def asset_by_serial(serial: str):
    item = buscar_equipamento_por_serial(serial)
    if not item:
        return {"found": False, "serial": serial.strip().upper(), "asset": None}
    return {"found": True, "serial": item.get("serial") or serial.strip().upper(), "asset": item}
