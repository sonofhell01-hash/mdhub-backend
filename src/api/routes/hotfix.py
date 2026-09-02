from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse

from src.core.config import settings


router = APIRouter(prefix="/hotfix", tags=["Hotfix Desktop"])


def _hotfix_root() -> Path:
    return Path.cwd() / "data" / "hotfix"


def _package_url(filename: str) -> str:
    return f"/hotfix/client-packages/{filename}"


def _normalize_manifest(data: dict[str, Any]) -> dict[str, Any]:
    """Keep older clients and newer auto-updaters reading the same manifest."""
    manifest = dict(data)
    package = str(manifest.get("package") or "").strip()
    install = dict(manifest.get("install") or {})
    notes = " ".join(str(note) for note in manifest.get("notes") or [])
    target = str(manifest.get("target") or "").strip().lower()
    package_lower = package.lower()
    is_client_package = (
        target in {"desktop", "client"}
        or package_lower.endswith((".exe", ".msi"))
        or str(install.get("type") or "").lower() in {"windows_installer", "msi_silent", "exe_silent"}
    )
    if "nao contem instalador exe de cliente" in notes.lower() or "servidor" in package_lower:
        is_client_package = False

    if package:
        public_url = str(manifest.get("url") or manifest.get("package_url") or _package_url(package))
        manifest["url"] = public_url
        manifest["package_url"] = public_url
        manifest["download_url"] = public_url

    mandatory = bool(manifest.get("mandatory") or manifest.get("push_install") or manifest.get("force_update"))
    manifest["mandatory"] = mandatory
    manifest["push_install"] = mandatory
    manifest["force_update"] = mandatory
    manifest.setdefault("target", "desktop" if is_client_package else "server")
    manifest.setdefault("channel", "stable")

    if package and package.lower().endswith((".exe", ".msi")):
        install.setdefault("type", "windows_installer")
        install.setdefault("requires_app_exit", True)
        install.setdefault("requires_restart", True)
    elif package:
        install.setdefault("type", "powershell_hotfix")
    if install:
        manifest["install"] = install

    manifest["client_update"] = {
        "available": bool(package and is_client_package),
        "mandatory": mandatory,
        "version": manifest.get("version"),
        "package": package or None,
        "url": manifest.get("url"),
        "sha256": manifest.get("sha256"),
        "size": manifest.get("size"),
        "install": manifest.get("install"),
    }
    return manifest


def _require_ingest_token(authorization: str | None) -> None:
    if not settings.sync_ingest_token:
        return
    if authorization != f"Bearer {settings.sync_ingest_token}":
        raise HTTPException(status_code=401, detail="Nao autenticado")


def read_hotfix_manifest() -> dict[str, Any]:
    path = _hotfix_root() / "manifest.json"
    if not path.exists():
        return {
            "status": "empty",
            "current_version": settings.app_version,
            "hotfix": None,
        }

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Manifesto de hotfix invalido: {exc}") from exc
    data = _normalize_manifest(data)

    return {
        "status": "ok",
        "current_version": settings.app_version,
        "hotfix": data,
    }


@router.get("/manifest")
def hotfix_manifest(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_ingest_token(authorization)
    return read_hotfix_manifest()


@router.get("/packages/{filename}")
def hotfix_package(
    filename: str,
    authorization: str | None = Header(default=None),
):
    _require_ingest_token(authorization)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nome de pacote invalido")

    path = _hotfix_root() / "packages" / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Pacote de hotfix nao encontrado")

    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
    )


@router.get("/client-manifest")
def client_hotfix_manifest() -> dict[str, Any]:
    """Manifesto consumido pelos clients desktop.

    Este endpoint nao exige bearer para permitir atualizacao automatica dos
    clients em maquinas regionais. O manifest nao deve conter segredos.
    """
    return read_hotfix_manifest()


@router.get("/client-packages/{filename}")
def client_hotfix_package(filename: str):
    """Download publico interno do pacote anunciado no manifest."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nome de pacote invalido")

    path = _hotfix_root() / "packages" / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Pacote de hotfix nao encontrado")

    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
    )
