from typing import Any

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db_session import get_db
from src.models.core import CheckerState
from src.services.automatos import AutomatosApiError, AutomatosClient
from src.services.automatos.snapshot_store import sync_automatos_snapshot
from src.services.evidence import ingest_automatos_rows, ingest_midiasimples_rows
from src.services.midiasimples.session_store import get_session


router = APIRouter(prefix="/checkers", tags=["Checkers"])


MIDIASIMPLES_MODULES = {
    "concessoes": "/colaboradores-tim",
    "devolucoes": "/termo-de-devolucao",
    "emprestimos": "/loan-term",
    "rats": "/rat-attendance",
    "laudos": "/laudo-tecnico-tim",
}

def _trim_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "action"}


def _row_cursor(row: dict[str, Any]) -> str:
    for key in ("id", "updated_at", "created_at"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _upsert_state(
    db: Session,
    *,
    fonte: str,
    modulo: str,
    status: str,
    total: int,
    count: int,
    payload: dict[str, Any] | None = None,
    ultimo_cursor: str | None = None,
    erro: str | None = None,
) -> CheckerState:
    state = db.query(CheckerState).filter(CheckerState.fonte == fonte, CheckerState.modulo == modulo).first()
    if not state:
        state = CheckerState(fonte=fonte, modulo=modulo)
        db.add(state)
    state.status = status
    state.total_registros = total
    state.ultima_contagem = count
    state.payload = payload
    state.ultimo_cursor = ultimo_cursor
    state.ultimo_erro = erro
    state.checked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(state)
    return state


def _serialize_state(state: CheckerState) -> dict[str, Any]:
    return {
        "id": state.id,
        "fonte": state.fonte,
        "modulo": state.modulo,
        "status": state.status,
        "ultimo_cursor": state.ultimo_cursor,
        "total_registros": state.total_registros,
        "ultima_contagem": state.ultima_contagem,
        "payload": state.payload,
        "ultimo_erro": state.ultimo_erro,
        "checked_at": state.checked_at.isoformat() if state.checked_at else None,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }


@router.get("/status")
def checker_status(email: str | None = Query(None, description="E-mail do tecnico logado no MidiaSimples")):
    stored = get_session(email)
    return {
        "midiasimples": {
            "base_url": settings.midiasimples_base_url,
            "session_active": bool(stored),
            "session_email": stored.email if stored else None,
            "last_used_at": stored.last_used_at.isoformat() if stored else None,
        },
        "automatos": {
            "base_url": settings.automatos_base_url,
            "configured": bool(settings.automatos_id and settings.automatos_security_key),
        },
        "sync": {
            "hub_configured": bool(settings.sync_hub_url),
        },
    }


@router.get("/states")
def checker_states(db: Session = Depends(get_db)):
    states = db.query(CheckerState).order_by(CheckerState.fonte.asc(), CheckerState.modulo.asc()).all()
    return {"items": [_serialize_state(state) for state in states]}


@router.get("/midiasimples/latest")
def midiasimples_latest(
    email: str = Query(..., description="E-mail do tecnico com sessao ativa"),
    module: str = Query("concessoes", description="concessoes, devolucoes, emprestimos, rats ou laudos"),
    length: int = Query(5, ge=1, le=25),
    persist: bool = Query(True, description="Gravar estado do checker no banco central"),
    db: Session = Depends(get_db),
):
    path = MIDIASIMPLES_MODULES.get(module)
    if not path:
        raise HTTPException(status_code=400, detail=f"Modulo invalido: {module}")
    stored = get_session(email)
    if not stored:
        raise HTTPException(status_code=401, detail="Sessao MidiaSimples nao esta ativa para este tecnico.")
    try:
        payload = stored.session.datatable(path, search="", start=0, length=length)
    except Exception as exc:
        if persist:
            _upsert_state(
                db,
                fonte="midiasimples",
                modulo=module,
                status="erro",
                total=0,
                count=0,
                erro=str(exc),
            )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rows = payload.get("data") or []
    evidences = ingest_midiasimples_rows(db, module, rows) if persist else 0
    response = {
        "module": module,
        "path": path,
        "records_total": payload.get("recordsTotal"),
        "records_filtered": payload.get("recordsFiltered"),
        "count": len(rows),
        "evidences": evidences,
        "items": [_trim_row(row) for row in rows],
    }
    if persist:
        _upsert_state(
            db,
            fonte="midiasimples",
            modulo=module,
            status="ok",
            total=int(payload.get("recordsTotal") or 0),
            count=len(rows),
            ultimo_cursor=_row_cursor(rows[0]) if rows else None,
            payload={
                "path": path,
                "records_filtered": payload.get("recordsFiltered"),
                "evidences": evidences,
                "sample": response["items"][:3],
            },
        )
    return response


@router.post("/midiasimples/run")
def run_midiasimples_checkers(
    email: str = Query(..., description="E-mail do tecnico com sessao ativa"),
    length: int = Query(5, ge=1, le=25),
    db: Session = Depends(get_db),
):
    results = []
    for module in MIDIASIMPLES_MODULES:
        try:
            results.append(midiasimples_latest(email=email, module=module, length=length, persist=True, db=db))
        except HTTPException as exc:
            results.append({"module": module, "status": "erro", "detail": exc.detail})
    return {
        "status": "ok",
        "processed": len(results),
        "results": results,
    }


@router.get("/automatos/latest")
def automatos_latest(
    length: int = Query(5, ge=1, le=25),
    persist: bool = Query(True, description="Gravar estado do checker no banco central"),
    ingest_evidences: bool = Query(False, description="Tambem gravar evidencias centrais completas"),
    db: Session = Depends(get_db),
):
    client = AutomatosClient()
    try:
        snapshot = client.get_desktops()
    except AutomatosApiError as exc:
        detail = str(exc)
        if persist:
            _upsert_state(
                db,
                fonte="automatos",
                modulo="desktops",
                status="erro",
                total=0,
                count=0,
                erro=detail,
            )
        status_code = 400 if "nao configurados" in detail else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc

    sample = snapshot.sample(length)
    operational_snapshot = sync_automatos_snapshot(snapshot.rows) if persist else 0
    evidences = ingest_automatos_rows(db, snapshot.rows) if persist and ingest_evidences else 0
    response_payload = {
        "module": "desktops",
        "path": snapshot.path,
        "records_total": snapshot.total,
        "count": len(sample),
        "evidences": evidences,
        "operational_snapshot": operational_snapshot,
        "latest_collect_date": snapshot.latest_collect_date,
        "latest_update_date": snapshot.latest_update_date,
        "items": sample,
    }
    if persist:
        _upsert_state(
            db,
            fonte="automatos",
            modulo="desktops",
            status="ok",
            total=snapshot.total,
            count=len(sample),
            ultimo_cursor=snapshot.cursor,
            payload={
                "path": snapshot.path,
                "evidences": evidences,
                "operational_snapshot": operational_snapshot,
                "ingest_evidences": ingest_evidences,
                "latest_collect_date": snapshot.latest_collect_date,
                "latest_update_date": snapshot.latest_update_date,
                "sample": sample[:3],
            },
        )
    return response_payload


@router.post("/automatos/run")
def run_automatos_checker(
    length: int = Query(5, ge=1, le=25),
    ingest_evidences: bool = Query(False, description="Tambem gravar evidencias centrais completas"),
    db: Session = Depends(get_db),
):
    try:
        result = automatos_latest(length=length, persist=True, ingest_evidences=ingest_evidences, db=db)
        return {
            "status": "ok",
            "processed": 1,
            "results": [result],
        }
    except HTTPException as exc:
        return {
            "status": "erro",
            "processed": 1,
            "results": [{"module": "desktops", "status": "erro", "detail": exc.detail}],
        }
