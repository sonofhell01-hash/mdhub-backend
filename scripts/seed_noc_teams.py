"""Seed idempotente das equipes e usuarios da Central NOC (linha de comando).

A logica em si vive em `src.services.noc_seed.seed_noc_teams`, compartilhada
com o endpoint `POST /database/seed/noc`. Este script so abre a sessao,
chama a funcao e faz commit.

Uso:
    python scripts/seed_noc_teams.py

Requer que as migrations (incluindo 20260903_0008_noc_teams) ja tenham sido
aplicadas (ver `POST /database/migrate`). Usa a mesma engine/sessao da
aplicacao (`src.core.db_session`), entao respeita o `DATABASE_URL` do
ambiente atual.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.db_session import SessionLocal  # noqa: E402
from src.services.noc_seed import seed_noc_teams  # noqa: E402


def main() -> dict[str, int]:
    db = SessionLocal()
    try:
        stats = seed_noc_teams(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return stats


if __name__ == "__main__":
    result = main()
    print("Seed NOC concluido:")
    for key, value in result.items():
        print(f"  {key}: {value}")
