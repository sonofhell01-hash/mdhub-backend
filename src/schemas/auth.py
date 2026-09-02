from pydantic import BaseModel


class MidiaSimplesLoginRequest(BaseModel):
    email: str
    password: str
    remember: bool = True


class MidiaSimplesLoginResponse(BaseModel):
    authenticated: bool
    base_url: str
    user_name: str | None = None
    technician_known: bool = False
    technician: dict | None = None
    access_token: str | None = None
    token_type: str | None = None
