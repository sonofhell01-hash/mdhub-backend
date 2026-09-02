from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db_session import get_db
from src.models.core import User


ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email.lower(),
        "scope": "mdhub",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Sessao do HUB nao informada")
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", ""))
        email = str(payload.get("email", "")).strip().lower()
        scope = payload.get("scope")
    except (JWTError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Sessao do HUB invalida ou expirada") from exc
    if scope != "mdhub" or not email:
        raise HTTPException(status_code=401, detail="Sessao do HUB invalida")
    user = db.get(User, user_id)
    if not user or not user.ativo or user.email.lower() != email:
        raise HTTPException(status_code=401, detail="Usuario do HUB invalido ou inativo")
    return user
