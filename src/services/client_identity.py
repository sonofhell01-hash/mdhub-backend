from __future__ import annotations

import getpass
import os
import platform
import socket
import uuid
from pathlib import Path
from typing import Any

from src.core.config import settings


def _identity_dir() -> Path:
    program_data = os.getenv("PROGRAMDATA")
    if program_data:
        return Path(program_data) / "MDHUB"

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "MD_HUB_FINAL"

    return Path.cwd() / "data" / "runtime"


def client_id_path() -> Path:
    return _identity_dir() / "client_id.txt"


def get_or_create_client_id() -> str:
    path = client_id_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value

    value = f"mdhub-{uuid.uuid4()}"
    path.write_text(value, encoding="utf-8")
    return value


def local_machine_identity(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "client_id": get_or_create_client_id(),
        "hostname": socket.gethostname(),
        "windows_user": getpass.getuser(),
        "app_version": settings.app_version,
        "backend_version": settings.app_version,
        "install_mode": "desktop",
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    if extra:
        payload.update({k: v for k, v in extra.items() if v is not None})
    return payload
