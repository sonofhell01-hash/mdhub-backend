from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.auth import create_access_token
from src.core.db_session import get_db
from src.models.core import User
from src.schemas.auth import MidiaSimplesLoginRequest, MidiaSimplesLoginResponse
from src.services.midiasimples.client import MidiaSimplesSession
from src.services.midiasimples.session_store import store_session
from src.services.technicians import get_technician_by_email


router = APIRouter(prefix="/auth", tags=["Autenticacao"])


@router.post("/midiasimples/login", response_model=MidiaSimplesLoginResponse)
def login_midiasimples(body: MidiaSimplesLoginRequest, db: Session = Depends(get_db)):
    session = MidiaSimplesSession()
    try:
        result = session.login(body.email, body.password)
        valid, message = session.validate_authenticated("/colaboradores-tim")
        if not valid:
            raise RuntimeError(
                f"Login recebeu resposta do MidiaSimples, mas a sessao nao ficou autenticada. {message}"
            )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    store_session(body.email, session, result.user_name, password=body.password, remember=body.remember)
    technician = get_technician_by_email(body.email)
    user = (
        db.query(User)
        .filter(func.lower(User.email) == body.email.strip().lower())
        .first()
        if technician
        else None
    )
    access_token = None
    if user and user.ativo:
        user.ultimo_login = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        access_token = create_access_token(user)
    return MidiaSimplesLoginResponse(
        authenticated=result.authenticated,
        base_url=result.base_url,
        user_name=result.user_name,
        technician_known=technician is not None,
        technician=technician.model_dump() if technician else None,
        access_token=access_token,
        token_type="bearer" if access_token else None,
    )
