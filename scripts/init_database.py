import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.db_session import SessionLocal, create_database_schema  # noqa: E402
from src.models.core import User  # noqa: E402
from src.services.technicians import list_technicians  # noqa: E402


def main() -> int:
    create_database_schema()
    with SessionLocal() as db:
        created = 0
        updated = 0
        for technician in list_technicians():
            user = db.query(User).filter(User.email == technician.email).first()
            if user:
                user.nome = technician.full_name
                user.apelido = technician.display_name
                user.midiasimples_id = technician.midiasimples_id
                user.perfil = "tecnico"
                user.ativo = technician.active
                updated += 1
            else:
                db.add(
                    User(
                        nome=technician.full_name,
                        apelido=technician.display_name,
                        email=technician.email,
                        midiasimples_id=technician.midiasimples_id,
                        perfil="tecnico",
                        ativo=technician.active,
                    )
                )
                created += 1
        db.commit()
    print(f"Schema OK. Tecnicos criados: {created}. Atualizados: {updated}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
