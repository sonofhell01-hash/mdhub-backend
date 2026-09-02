import shutil
import sqlite3
from pathlib import Path

from src.core.config import settings


def project_root() -> Path:
    # backend/src/core/database.py -> backend/
    return Path(__file__).resolve().parents[2]


def _bundled_seed_path() -> Path:
    return project_root() / "seed" / "clientes_rat_seed.db"


def shared_db_path() -> Path:
    """Resolve where the legacy operational-lookup SQLite database lives.

    On a normal server/desktop deploy this is just LOCAL_SQLITE_PATH (writable disk).
    On Vercel (or any serverless runtime with a read-only deployment bundle) only /tmp is
    writable, so we copy a read-only seed database bundled with the deployment into /tmp on
    first access and use that copy instead. If no seed database was bundled, this behaves
    exactly like before: the caller gets "banco local nao encontrado" until one is provided.
    """
    configured = Path(settings.local_sqlite_path)
    path = configured if configured.is_absolute() else project_root() / configured

    if not settings.is_serverless:
        return path

    tmp_path = Path("/tmp") / "mdhub" / "clientes_rat.db"
    if tmp_path.exists():
        return tmp_path

    seed = _bundled_seed_path()
    if seed.exists():
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed, tmp_path)
        return tmp_path

    # No writable copy and nothing bundled to seed from: report the (unreachable) configured
    # path so callers keep returning the same "missing db" response as before.
    return path


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or shared_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def execute_script(sql: str, db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(sql)
        conn.commit()
