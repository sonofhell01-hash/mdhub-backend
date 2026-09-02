from src.schemas.technician import Technician


TECHNICIANS: tuple[Technician, ...] = (
    Technician(
        username="marcel.silva",
        display_name="Marcel Silva",
        full_name="MARCEL DIEGO SILVA",
        midiasimples_id=308,
        email="marcel.silva@arklok.com.br",
    ),
    Technician(
        username="caio.freitas",
        display_name="Caio Freitas",
        full_name="Caio Vinicius Pereira da Silva Freitas",
        midiasimples_id=332,
        email="caio.freitas@arklok.com.br",
    ),
    Technician(
        username="michel.delocco",
        display_name="Michel Delocco",
        full_name="MICHEL PURCINA DELOCCO",
        midiasimples_id=148,
        email="michel.delocco@arklok.com.br",
    ),
    Technician(
        username="marcos.reis",
        display_name="Marcos Reis",
        full_name="Marcos Paulo da Silva Reis",
        midiasimples_id=227,
        email="marcos.reis@arklok.com.br",
    ),
    Technician(
        username="bruno.rodrigues",
        display_name="Bruno Rodrigues",
        full_name="Bruno Rodrigues da Silva",
        midiasimples_id=410,
        email="bruno.rodrigues@arklok.com.br",
    ),
)


def list_technicians() -> list[Technician]:
    return list(TECHNICIANS)


def get_technician_by_email(email: str) -> Technician | None:
    normalized = (email or "").strip().lower()
    for technician in TECHNICIANS:
        if technician.email.lower() == normalized:
            return technician
    return None


def get_technician_by_midiasimples_id(midiasimples_id: int) -> Technician | None:
    for technician in TECHNICIANS:
        if technician.midiasimples_id == midiasimples_id:
            return technician
    return None
