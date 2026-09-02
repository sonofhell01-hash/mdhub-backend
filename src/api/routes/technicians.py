from fastapi import APIRouter, HTTPException, Query

from src.schemas.technician import Technician
from src.services.technicians import (
    get_technician_by_email,
    get_technician_by_midiasimples_id,
    list_technicians,
)


router = APIRouter(prefix="/tecnicos", tags=["Tecnicos"])


@router.get("", response_model=list[Technician])
def get_technicians():
    return list_technicians()


@router.get("/lookup", response_model=Technician)
def lookup_technician(
    email: str | None = Query(default=None),
    midiasimples_id: int | None = Query(default=None),
):
    technician = None

    if email:
        technician = get_technician_by_email(email)

    if not technician and midiasimples_id is not None:
        technician = get_technician_by_midiasimples_id(midiasimples_id)

    if not technician:
        raise HTTPException(status_code=404, detail="Tecnico nao encontrado")

    return technician
