from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.core.db_session import SessionLocal
from src.core.config import settings
from src.models.core import MidiaSimplesSessionCache
from src.services.midiasimples.client import MidiaSimplesSession


@dataclass
class StoredMidiaSession:
    email: str
    session: MidiaSimplesSession
    user_name: str | None
    created_at: datetime
    last_used_at: datetime


_SESSIONS_BY_EMAIL: dict[str, StoredMidiaSession] = {}


def _key(email: str | None) -> str:
    return (email or "").strip().lower()


def store_session(
    email: str,
    session: MidiaSimplesSession,
    user_name: str | None = None,
    *,
    password: str | None = None,
    remember: bool = True,
) -> StoredMidiaSession:
    now = datetime.now(timezone.utc)
    stored = StoredMidiaSession(
        email=_key(email),
        session=session,
        user_name=user_name,
        created_at=now,
        last_used_at=now,
    )
    _SESSIONS_BY_EMAIL[stored.email] = stored
    _persist_session(stored, password=password if remember else None)
    return stored


def get_session(email: str | None) -> StoredMidiaSession | None:
    key = _key(email)
    stored = _SESSIONS_BY_EMAIL.get(key)
    if not stored and key:
        stored = _restore_session(key)
        if stored:
            _SESSIONS_BY_EMAIL[key] = stored
    if stored:
        stored.last_used_at = datetime.now(timezone.utc)
        _touch_session(stored)
    return stored


def get_validated_session(
    email: str | None,
    path: str = "/colaboradores-tim",
    request_timeout: int = 45,
) -> StoredMidiaSession | None:
    stored = get_session(email)
    if not stored:
        return refresh_session(email, path=path, request_timeout=request_timeout)
    valid, _message = stored.session.validate_authenticated(path, timeout=request_timeout)
    if not valid:
        invalidate_session(email)
        return refresh_session(email, path=path, request_timeout=request_timeout)
    return stored


def get_recent_validated_session(path: str = "/colaboradores-tim") -> StoredMidiaSession | None:
    """Retorna a sessao ativa mais recente para consultas operacionais.

    A consulta operacional nao sabe, por si so, qual tecnico esta usando a tela.
    Em ambiente centralizado isso permite reaproveitar a ultima sessao valida do
    MidiaSimples para completar nome, email, cargo e termo atual sem obrigar o
    usuario a informar credenciais a cada busca.
    """
    candidates = sorted(_SESSIONS_BY_EMAIL.values(), key=lambda item: item.last_used_at, reverse=True)
    for stored in candidates:
        valid, _message = stored.session.validate_authenticated(path)
        if valid:
            return stored
        invalidate_session(stored.email)

    db = SessionLocal()
    try:
        rows = (
            db.query(MidiaSimplesSessionCache)
            .filter(MidiaSimplesSessionCache.status == "ativa")
            .order_by(MidiaSimplesSessionCache.last_used_at.desc().nullslast())
            .limit(5)
            .all()
        )
        emails = [row.email for row in rows if row.email]
    finally:
        db.close()

    for email in emails:
        stored = get_validated_session(email, path=path)
        if stored:
            return stored
    return refresh_session(settings.midiasimples_email, path=path)


def has_session(email: str | None) -> bool:
    return get_session(email) is not None


def invalidate_session(email: str | None, reason: str = "expirada") -> None:
    key = _key(email)
    if key:
        _SESSIONS_BY_EMAIL.pop(key, None)
    db = SessionLocal()
    try:
        row = db.query(MidiaSimplesSessionCache).filter(MidiaSimplesSessionCache.email == key).first() if key else None
        if row:
            row.status = reason
            db.commit()
    finally:
        db.close()


def refresh_session(
    email: str | None,
    path: str = "/colaboradores-tim",
    request_timeout: int = 45,
) -> StoredMidiaSession | None:
    key = _key(email)
    login_email, password = _stored_credentials(key)
    if not login_email or not password:
        return None

    session = MidiaSimplesSession()
    try:
        result = session.login(login_email, password)
        valid, _message = session.validate_authenticated(path, timeout=request_timeout)
    except Exception:
        return None
    if not valid:
        return None
    return store_session(login_email, session, result.user_name, password=password, remember=True)


def _persist_session(stored: StoredMidiaSession, password: str | None = None) -> None:
    db = SessionLocal()
    try:
        row = db.query(MidiaSimplesSessionCache).filter(MidiaSimplesSessionCache.email == stored.email).first()
        if not row:
            row = MidiaSimplesSessionCache(email=stored.email, base_url=stored.session.base_url)
            db.add(row)
        row.user_name = stored.user_name
        row.base_url = stored.session.base_url
        session_data = stored.session.export_state()
        previous_auth = (row.session_data or {}).get("auth") if row.session_data else None
        if password:
            session_data["auth"] = {"email": stored.email, "password": password}
        elif previous_auth:
            session_data["auth"] = previous_auth
        row.session_data = session_data
        row.status = "ativa"
        row.last_used_at = stored.last_used_at.replace(tzinfo=None)
        row.expires_at = (stored.last_used_at + timedelta(hours=8)).replace(tzinfo=None)
        db.commit()
    finally:
        db.close()


def _touch_session(stored: StoredMidiaSession) -> None:
    db = SessionLocal()
    try:
        row = db.query(MidiaSimplesSessionCache).filter(MidiaSimplesSessionCache.email == stored.email).first()
        if row:
            row.last_used_at = stored.last_used_at.replace(tzinfo=None)
            db.commit()
    finally:
        db.close()


def _restore_session(email: str) -> StoredMidiaSession | None:
    db = SessionLocal()
    try:
        row = db.query(MidiaSimplesSessionCache).filter(MidiaSimplesSessionCache.email == email).first()
        if not row or row.status != "ativa":
            return None
        if row.expires_at and row.expires_at < datetime.now():
            row.status = "expirada"
            db.commit()
            return None
        session = MidiaSimplesSession.from_state(row.session_data or {})
        now = datetime.now(timezone.utc)
        return StoredMidiaSession(
            email=email,
            session=session,
            user_name=row.user_name,
            created_at=(row.created_at.replace(tzinfo=timezone.utc) if row.created_at else now),
            last_used_at=now,
        )
    finally:
        db.close()


def _stored_credentials(email: str) -> tuple[str, str]:
    db = SessionLocal()
    try:
        row = db.query(MidiaSimplesSessionCache).filter(MidiaSimplesSessionCache.email == email).first() if email else None
        auth = (row.session_data or {}).get("auth") if row and row.session_data else None
        login_email = _key((auth or {}).get("email") or settings.midiasimples_email or email)
        password = str((auth or {}).get("password") or settings.midiasimples_password or "")
        return login_email, password
    finally:
        db.close()
