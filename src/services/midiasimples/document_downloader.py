from __future__ import annotations

import re
import hashlib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.services.midiasimples.client import MidiaSimplesSession


MIDIASIMPLES_DOCUMENT_PATHS = {
    "rat": "/rat-attendance",
    "laudo": "/laudo-tecnico-tim",
    "concessao": "/colaboradores-tim",
    "emprestimo": "/loan-term",
    "devolucao": "/termo-de-devolucao",
}


@dataclass(frozen=True)
class MidiaSimplesDownloadResult:
    tipo: str
    midiasimples_id: str
    status: int
    final_url: str
    content_type: str
    content_disposition: str
    filename: str
    path: Path
    bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "tipo": self.tipo,
            "midiasimples_id": self.midiasimples_id,
            "http_status": self.status,
            "final_url": self.final_url,
            "content_type": self.content_type,
            "content_disposition": self.content_disposition,
            "filename": self.filename,
            "path": str(self.path),
            "bytes": self.bytes,
        }


def document_path_for_type(tipo: str) -> str:
    key = str(tipo or "").strip().lower()
    path = MIDIASIMPLES_DOCUMENT_PATHS.get(key)
    if not path:
        raise ValueError(f"Tipo de documento sem download automatico: {tipo}")
    return path


def download_midiasimples_document(
    session: MidiaSimplesSession,
    *,
    tipo: str,
    midiasimples_id: str,
    output_root: Path | None = None,
    preferred_urls: list[str] | tuple[str, ...] | None = None,
) -> MidiaSimplesDownloadResult:
    doc_type = str(tipo or "").strip().lower()
    doc_id = str(midiasimples_id or "").strip()
    if not doc_id:
        raise ValueError("ID do documento MidiaSimples nao informado.")

    base_path = document_path_for_type(doc_type)
    output_root = output_root or Path.cwd() / "data" / "downloads" / "midiasimples"
    output_dir = output_root / doc_type
    output_dir.mkdir(parents=True, exist_ok=True)

    urls: list[str] = []
    for preferred_url in preferred_urls or ():
        url = str(preferred_url or "").strip()
        if not url:
            continue
        urls.append(urllib.parse.urljoin(session.base_url + "/", url))

    if doc_type == "concessao":
        urls.append(f"{session.base_url}{base_path}/{doc_id}/docusign?archive=combined")

    urls.extend(
        [
            f"{session.base_url}{base_path}/{doc_id}/docusign/download",
            f"{session.base_url}{base_path}/{doc_id}/download/docusign",
            f"{session.base_url}{base_path}/{doc_id}/download",
            f"{session.base_url}{base_path}/{doc_id}",
        ]
    )
    last_error = ""
    for url in urls:
        try:
            return _download_file(session, url=url, referer=f"{session.base_url}{base_path}", doc_type=doc_type, doc_id=doc_id, output_dir=output_dir)
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code} ao baixar {url}"
            continue
        except ValueError as exc:
            last_error = str(exc)
            if "PDF assinado" not in last_error and "HTML em vez" not in last_error:
                raise

    raise ValueError(last_error or "Nao foi possivel baixar o PDF assinado do documento.")


def _download_file(
    session: MidiaSimplesSession,
    *,
    url: str,
    referer: str,
    doc_type: str,
    doc_id: str,
    output_dir: Path,
) -> MidiaSimplesDownloadResult:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MD-HUB-FINAL/2026",
            "Accept": (
                "application/pdf,"
                "application/octet-stream,"
                "text/html;q=0.5,*/*;q=0.1"
            ),
            "Referer": referer,
        },
        method="GET",
    )
    response = session.opener.open(request, timeout=90)
    body = response.read()
    headers = dict(response.headers)
    final_url = response.geturl()
    content_type = headers.get("Content-Type", "")
    content_disposition = headers.get("Content-Disposition", "")

    if body.lstrip().startswith(b"<") or "text/html" in content_type.lower():
        text = body.decode("utf-8-sig", errors="replace")
        if session.is_login_response(final_url, text):
            raise ValueError("Sessao MidiaSimples expirada ao tentar baixar documento.")
        raise ValueError("MidiaSimples retornou HTML em vez do arquivo do documento.")

    if not (body.startswith(b"%PDF") or "pdf" in content_type.lower()):
        raise ValueError("MidiaSimples nao retornou o PDF assinado; download bruto/DOCX ignorado.")

    filename = _filename_from_headers(content_disposition)
    if not filename:
        filename = f"{doc_type}_{doc_id}{_extension_for(content_type, body)}"
    filename = _safe_filename(filename)
    path = output_dir / filename
    path.write_bytes(body)

    return MidiaSimplesDownloadResult(
        tipo=doc_type,
        midiasimples_id=doc_id,
        status=int(response.status),
        final_url=final_url,
        content_type=content_type,
        content_disposition=content_disposition,
        filename=filename,
        path=path.resolve(),
        bytes=len(body),
    )


def _filename_from_headers(content_disposition: str) -> str | None:
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition or "", re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _extension_for(content_type: str, body: bytes) -> str:
    lowered = (content_type or "").lower()
    if body.startswith(b"%PDF") or "pdf" in lowered:
        return ".pdf"
    if body.startswith(b"PK") or "wordprocessingml" in lowered:
        return ".docx"
    return ".bin"


def _safe_filename(filename: str, max_length: int = 140) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", filename).strip(" .")
    cleaned = cleaned or "documento.bin"
    if len(cleaned) <= max_length:
        return cleaned
    suffix = Path(cleaned).suffix[:16]
    stem = Path(cleaned).stem
    digest = hashlib.sha256(cleaned.encode("utf-8", errors="replace")).hexdigest()[:10]
    available = max(20, max_length - len(suffix) - len(digest) - 2)
    return f"{stem[:available].rstrip(' ._-')}__{digest}{suffix}"
