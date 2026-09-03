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


def _technician_payload_from_user(user: User) -> dict:
    """Monta um payload no formato `Technician` do frontend a partir de um
    usuario real do banco (`usuarios`), para qualquer usuario cadastrado -
    nao apenas os 5 da lista estatica legada em `src/services/technicians.py`.
    """
    email = user.email.strip().lower()
    return {
        "username": email.split("@")[0],
        "display_name": user.apelido or user.nome,
        "full_name": user.nome,
        "midiasimples_id": user.midiasimples_id or 0,
        "email": user.email,
        "active": user.ativo,
        # usuario_id (FK real de `usuarios.id`) - o frontend usa isso para
        # atribuir corretamente `documentos.usuario_id` ao criar qualquer
        # documento pelo HUB (ver DocumentWizard.tsx). NUNCA confundir com
        # midiasimples_id/id de colaborador retornados pela busca operacional.
        "usuario_id": user.id,
    }


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

    normalized_email = body.email.strip().lower()
    user = db.query(User).filter(func.lower(User.email) == normalized_email).first()

    access_token: str | None = None
    technician_payload: dict | None = None
    technician_known = False

    if user and user.ativo:
        # Fonte de verdade agora e o banco (`usuarios`), nao a lista estatica -
        # cobre os 21 usuarios da Central NOC e qualquer outro cadastrado depois.
        user.ultimo_login = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        access_token = create_access_token(user)
        technician_payload = _technician_payload_from_user(user)
        technician_known = True
    else:
        # Fallback: usuario ainda nao migrado para `usuarios` (ou inativo).
        # Mantem o comportamento legado baseado na lista estatica, para nao
        # quebrar login de quem ainda nao foi cadastrado no banco.
        technician = get_technician_by_email(body.email)
        if technician:
            technician_payload = technician.model_dump()
            technician_known = True

    return MidiaSimplesLoginResponse(
        authenticated=result.authenticated,
        base_url=result.base_url,
        user_name=result.user_name,
        technician_known=technician_known,
        technician=technician_payload,
        access_token=access_token,
        token_type="bearer" if access_token else None,
    )
