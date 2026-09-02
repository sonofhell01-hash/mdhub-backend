from fastapi import APIRouter, HTTPException

from src.schemas.midiasimples import DataTablePage, MidiaSimplesSearchRequest
from src.services.midiasimples.client import MidiaSimplesSession, normalize_datatable_response


router = APIRouter(prefix="/midiasimples", tags=["MidiaSimples"])


def _session_from_request(body: MidiaSimplesSearchRequest) -> MidiaSimplesSession:
    session = MidiaSimplesSession()
    session.login(body.email, body.password)
    return session


@router.post("/concessoes/search", response_model=DataTablePage)
def search_concessoes(body: MidiaSimplesSearchRequest):
    try:
        payload = _session_from_request(body).get_concessoes(
            search=body.search,
            start=body.start,
            length=body.length,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return normalize_datatable_response(payload)


@router.post("/devolucoes/search", response_model=DataTablePage)
def search_devolucoes(body: MidiaSimplesSearchRequest):
    try:
        payload = _session_from_request(body).get_devolucoes(
            search=body.search,
            start=body.start,
            length=body.length,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return normalize_datatable_response(payload)


@router.post("/emprestimos/search", response_model=DataTablePage)
def search_emprestimos(body: MidiaSimplesSearchRequest):
    try:
        payload = _session_from_request(body).get_emprestimos(
            search=body.search,
            start=body.start,
            length=body.length,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return normalize_datatable_response(payload)
