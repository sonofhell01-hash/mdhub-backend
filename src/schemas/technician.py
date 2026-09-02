from pydantic import BaseModel


class Technician(BaseModel):
    username: str
    display_name: str
    full_name: str
    midiasimples_id: int
    email: str
    active: bool = True
