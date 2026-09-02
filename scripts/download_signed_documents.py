from __future__ import annotations

import argparse
import csv
import html
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from src.core.config import settings  # noqa: E402
from src.services.midiasimples.document_downloader import download_midiasimples_document  # noqa: E402
from src.services.midiasimples.session_store import get_validated_session  # noqa: E402
from src.services.whatsapp.rat_reminders import (  # noqa: E402
    normalize_name,
    parse_midiasimples_date,
    pick,
    rat_is_signed,
)


DOCUMENT_TYPES = (
    {"tipo": "rat", "path": "/rat-attendance", "label": "RAT"},
    {"tipo": "laudo", "path": "/laudo-tecnico-tim", "label": "Laudo"},
    {"tipo": "concessao", "path": "/colaboradores-tim", "label": "Concessao"},
    {"tipo": "emprestimo", "path": "/loan-term", "label": "Emprestimo"},
    {"tipo": "devolucao", "path": "/termo-de-devolucao", "label": "Devolucao"},
)


@dataclass(frozen=True)
class SignedDocumentCandidate:
    tipo: str
    label: str
    path: str
    doc_id: str
    created_at_text: str
    created_at_sort: str
    technician: str
    status: str
    preferred_urls: tuple[str, ...]
    row: dict[str, Any]


@dataclass(frozen=True)
class UnsignedDocumentItem:
    tipo: str
    label: str
    path: str
    doc_id: str
    name: str
    created_at_text: str
    created_at_sort: str
    technician: str
    status: str
    row: dict[str, Any]


def configure_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        path = Path(log_file)
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                path,
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        handlers=handlers,
        force=True,
    )


def responsible_name(row: dict[str, Any]) -> str:
    return str(
        pick(
            row,
            "arklok_responsible",
            "technician_name",
            "technician",
            "tecnico",
            "responsible",
            "responsavel",
            "responsavel_arklok",
            "created_by",
            "usuario_criacao",
        )
        or ""
    ).strip()


def signature_status(row: dict[str, Any]) -> str:
    return str(
        pick(
            row,
            "docusign_status",
            "status_docusign",
            "document_status",
            "signature_status",
            "status_assinatura",
            "status",
        )
        or ""
    ).strip()


def created_at_text(row: dict[str, Any]) -> str:
    return str(
        pick(
            row,
            "created_at",
            "created",
            "creation_date",
            "created_date",
            "data_criacao",
            "date",
        )
        or ""
    ).strip()


def document_id(row: dict[str, Any]) -> str:
    value = pick(row, "id", "midiasimples_id", "document_id")
    if value not in (None, ""):
        return str(value).strip()

    action = str(row.get("action") or "")
    match = re.search(r"/(?:rat-attendance|laudo-tecnico-tim|colaboradores-tim|loan-term|termo-de-devolucao)/(\d+)", action)
    return match.group(1) if match else ""


def document_name(row: dict[str, Any]) -> str:
    return str(
        pick(
            row,
            "customer_name",
            "client_name",
            "name",
            "colaborador",
            "nome",
            "title",
            "document_name",
            "ticket",
            "numero_chamado",
        )
        or ""
    ).strip()


def matches_technician(row: dict[str, Any], technician: str | list[str] | tuple[str, ...] | set[str]) -> bool:
    technicians = [technician] if isinstance(technician, str) else list(technician or [])
    found = normalize_name(responsible_name(row))
    if not technicians or not found:
        return False
    for item in technicians:
        wanted = normalize_name(item)
        if wanted and (wanted == found or wanted in found or found in wanted):
            return True
    return False


def signed(row: dict[str, Any]) -> bool:
    status = normalize_name(signature_status(row))
    return rat_is_signed(row) or "COMPLET" in status or "ASSINAD" in status


def _is_pending_signature(row: dict[str, Any]) -> bool:
    status = normalize_name(signature_status(row))
    if not status:
        return True
    return not signed(row)


def extract_preferred_urls(row: dict[str, Any]) -> tuple[str, ...]:
    action = html.unescape(str(row.get("action") or ""))
    urls: list[str] = []
    for match in re.finditer(r"""href=["']([^"']+)["']""", action, flags=re.IGNORECASE):
        url = match.group(1).strip()
        lowered = url.lower()
        if "docusign" in lowered and "download" in lowered:
            urls.append(url)
    return tuple(dict.fromkeys(urls))


def collect_candidates(
    *,
    session: Any,
    technician: str | list[str] | tuple[str, ...] | set[str],
    max_pages: int,
    page_size: int,
    types: set[str],
) -> list[SignedDocumentCandidate]:
    candidates: list[SignedDocumentCandidate] = []
    _candidates, _unsigned = collect_documents(
        session=session,
        technician=technician,
        max_pages=max_pages,
        page_size=page_size,
        types=types,
    )
    return _candidates


def collect_documents(
    *,
    session: Any,
    technician: str | list[str] | tuple[str, ...] | set[str],
    max_pages: int,
    page_size: int,
    types: set[str],
) -> tuple[list[SignedDocumentCandidate], list[UnsignedDocumentItem]]:
    candidates: list[SignedDocumentCandidate] = []
    unsigned: list[UnsignedDocumentItem] = []

    for config in DOCUMENT_TYPES:
        tipo = str(config["tipo"])
        if tipo not in types:
            continue
        path = str(config["path"])
        label = str(config["label"])
        logging.info("Consultando %s em %s...", label, path)

        for page in range(max_pages):
            start = page * page_size
            payload = session.datatable(path, search="", start=start, length=page_size, order_by_id_desc=True)
            rows = payload.get("data") or []
            if not rows:
                logging.info("%s: pagina %s vazia; fim da consulta.", label, page + 1)
                break

            logging.info("%s: pagina %s com %s registros.", label, page + 1, len(rows))
            for row in rows:
                if not matches_technician(row, technician):
                    continue
                doc_id = document_id(row)
                status = signature_status(row)
                created_text = created_at_text(row)
                created_at = parse_midiasimples_date(created_text)
                logging.info(
                    "Candidato %s #%s | tecnico=%s | criado=%s | status=%s",
                    label,
                    doc_id or "sem-id",
                    responsible_name(row) or "sem tecnico",
                    created_text or "sem data",
                    status or "sem status",
                )

                if not doc_id:
                    logging.warning("%s ignorado: sem ID do MidiaSimples.", label)
                    continue
                if not signed(row):
                    logging.info("%s #%s ignorado: documento ainda nao esta assinado.", label, doc_id)
                    if _is_pending_signature(row):
                        unsigned.append(
                            UnsignedDocumentItem(
                                tipo=tipo,
                                label=label,
                                path=path,
                                doc_id=doc_id,
                                name=document_name(row),
                                created_at_text=created_text,
                                created_at_sort=created_at.isoformat() if created_at else "",
                                technician=responsible_name(row),
                                status=status,
                                row=row,
                            )
                        )
                    continue
                if not created_at:
                    logging.info("%s #%s ignorado: data de criacao invalida.", label, doc_id)
                    continue

                candidates.append(
                    SignedDocumentCandidate(
                        tipo=tipo,
                        label=label,
                        path=path,
                        doc_id=doc_id,
                        created_at_text=created_text,
                        created_at_sort=created_at.isoformat(),
                        technician=responsible_name(row),
                        status=status,
                        preferred_urls=extract_preferred_urls(row),
                        row=row,
                    )
                )

    return (
        sorted(candidates, key=lambda item: item.created_at_sort, reverse=True),
        sorted(unsigned, key=lambda item: item.created_at_sort, reverse=True),
    )


def write_unsigned_report(items: list[UnsignedDocumentItem], report_file: str | Path | None) -> Path | None:
    if not report_file:
        return None
    path = Path(report_file)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "tipo",
                "documento",
                "id",
                "nome",
                "tecnico",
                "status_assinatura",
                "criado_em",
                "path_midiasimples",
            ],
            delimiter=";",
        )
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "tipo": item.tipo,
                    "documento": item.label,
                    "id": item.doc_id,
                    "nome": item.name,
                    "tecnico": item.technician,
                    "status_assinatura": item.status,
                    "criado_em": item.created_at_text,
                    "path_midiasimples": item.path,
                }
            )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Baixa PDFs assinados da MidiaSimples com log visivel no terminal.")
    parser.add_argument("--technician", default="MARCEL DIEGO SILVA", help="Nome do tecnico/responsavel.")
    parser.add_argument("--technicians", nargs="+", default=None, help="Lista de tecnicos/responsaveis.")
    parser.add_argument("--limit", type=int, default=20, help="Quantidade de documentos assinados a baixar.")
    parser.add_argument("--max-pages", type=int, default=50, help="Maximo de paginas consultadas por tipo.")
    parser.add_argument("--page-size", type=int, default=100, help="Registros consultados por pagina.")
    parser.add_argument("--email", default=settings.midiasimples_email, help="E-mail da sessao MidiaSimples.")
    parser.add_argument("--output-root", default="data/downloads/signed_pdfs", help="Pasta raiz dos PDFs baixados.")
    parser.add_argument("--log-file", default="data/runtime/logs/download_signed_documents.log", help="Arquivo de log opcional.")
    parser.add_argument("--types", nargs="+", default=["rat", "laudo", "concessao"], choices=["rat", "laudo", "concessao", "emprestimo", "devolucao"])
    parser.add_argument("--unsigned-report-file", default="data/runtime/logs/download_worker_unsigned_report.csv", help="Relatorio CSV dos documentos encontrados ainda nao assinados.")
    parser.add_argument("--dry-run", action="store_true", help="Lista os documentos sem baixar.")
    args = parser.parse_args()

    configure_logging(args.log_file)
    logging.info("Download de PDFs assinados iniciado.")
    technicians = args.technicians or [args.technician]
    logging.info("Tecnicos: %s | limite: %s | tipos: %s", ", ".join(technicians), args.limit, ", ".join(args.types))

    stored = get_validated_session(args.email, path="/rat-attendance", request_timeout=90)
    if not stored:
        logging.error("Sessao MidiaSimples nao esta ativa para %s.", args.email or "email nao informado")
        return 2

    candidates, unsigned = collect_documents(
        session=stored.session,
        technician=technicians,
        max_pages=args.max_pages,
        page_size=args.page_size,
        types=set(args.types),
    )
    report_path = write_unsigned_report(unsigned, args.unsigned_report_file)
    if report_path:
        logging.info("Relatorio de nao assinados: %s itens | %s", len(unsigned), report_path.resolve())
    selected = candidates[: max(args.limit, 0)]
    logging.info("Encontrados %s documentos assinados do tecnico. Selecionados %s.", len(candidates), len(selected))

    if not selected:
        return 0

    output_root = Path(args.output_root)
    downloaded = 0
    failed = 0
    for index, item in enumerate(selected, start=1):
        logging.info(
            "[%02d/%02d] Baixando %s #%s | criado=%s | status=%s | tecnico=%s",
            index,
            len(selected),
            item.label,
            item.doc_id,
            item.created_at_text,
            item.status,
            item.technician,
        )
        if args.dry_run:
            logging.info("[%02d/%02d] Dry-run: download ignorado.", index, len(selected))
            continue

        try:
            result = download_midiasimples_document(
                stored.session,
                tipo=item.tipo,
                midiasimples_id=item.doc_id,
                output_root=output_root,
                preferred_urls=item.preferred_urls,
            )
        except Exception as exc:
            failed += 1
            logging.exception("[%02d/%02d] Falha ao baixar %s #%s: %s", index, len(selected), item.label, item.doc_id, exc)
            continue

        downloaded += 1
        logging.info(
            "[%02d/%02d] OK: %s | %s bytes | content-type=%s",
            index,
            len(selected),
            result.path,
            result.bytes,
            result.content_type or "sem content-type",
        )

    logging.info("Resumo: selecionados=%s | baixados=%s | falhas=%s", len(selected), downloaded, failed)
    logging.info("Pasta de saida: %s", output_root.resolve())
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
