from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from download_signed_documents import collect_documents, configure_logging, write_unsigned_report  # noqa: E402
from src.core.config import settings  # noqa: E402
from src.services.midiasimples.document_downloader import download_midiasimples_document  # noqa: E402
from src.services.midiasimples.session_store import get_validated_session  # noqa: E402
from src.services.whatsapp.rat_reminders import SIGNATURE_RADAR_TECHNICIANS  # noqa: E402


DEFAULT_TECHNICIANS = SIGNATURE_RADAR_TECHNICIANS


def _format_interval(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}min"
    return f"{seconds}s"


def _format_batch_limit(limit: int) -> str:
    if limit <= 0:
        return "sem limite"
    return str(limit)


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"downloaded": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logging.warning("Estado do worker invalido em %s; iniciando estado limpo.", path)
        return {"downloaded": {}}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _doc_key(tipo: str, doc_id: str) -> str:
    return f"{tipo}:{doc_id}"


def _run_download_cycle(
    *,
    email: str,
    technicians: list[str],
    max_pages: int,
    page_size: int,
    batch_limit: int,
    types: set[str],
    output_root: Path,
    state_path: Path,
    unsigned_report_path: Path,
) -> dict[str, Any]:
    stored = get_validated_session(email, path="/rat-attendance", request_timeout=90)
    if not stored:
        raise RuntimeError(f"Sessao MidiaSimples nao esta ativa para {email or 'email nao informado'}.")

    state = _load_state(state_path)
    downloaded_state = state.setdefault("downloaded", {})
    candidates, unsigned = collect_documents(
        session=stored.session,
        technician=technicians,
        max_pages=max_pages,
        page_size=page_size,
        types=types,
    )
    report_path = write_unsigned_report(unsigned, unsigned_report_path)
    if report_path:
        logging.info("Relatorio de nao assinados: %s itens | %s", len(unsigned), report_path.resolve())

    new_candidates = [item for item in candidates if _doc_key(item.tipo, item.doc_id) not in downloaded_state]
    selected = new_candidates if batch_limit <= 0 else new_candidates[:batch_limit]
    logging.info(
        "Verificacao concluida: assinados=%s | novos=%s | selecionados=%s | ja baixados=%s",
        len(candidates),
        len(new_candidates),
        len(selected),
        len(downloaded_state),
    )

    downloaded = 0
    failed = 0
    for index, item in enumerate(selected, start=1):
        key = _doc_key(item.tipo, item.doc_id)
        logging.info(
            "[%02d/%02d] Baixando %s #%s | criado=%s | tecnico=%s | status=%s",
            index,
            len(selected),
            item.label,
            item.doc_id,
            item.created_at_text,
            item.technician,
            item.status,
        )
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
            logging.exception("[%02d/%02d] Falha no download de %s #%s: %s", index, len(selected), item.label, item.doc_id, exc)
            continue

        downloaded += 1
        downloaded_state[key] = {
            "tipo": item.tipo,
            "doc_id": item.doc_id,
            "created_at": item.created_at_text,
            "technician": item.technician,
            "status": item.status,
            "path": str(result.path),
            "bytes": result.bytes,
            "content_type": result.content_type,
            "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        }
        _save_state(state_path, state)
        logging.info(
            "[%02d/%02d] OK: %s | %s bytes | %s",
            index,
            len(selected),
            result.path,
            result.bytes,
            result.content_type or "sem content-type",
        )

    state["last_run_at"] = datetime.now().isoformat(timespec="seconds")
    state["technicians"] = technicians
    state["types"] = sorted(types)
    _save_state(state_path, state)

    return {
        "candidates": len(candidates),
        "new": len(new_candidates),
        "selected": len(selected),
        "downloaded": downloaded,
        "failed": failed,
        "unsigned": len(unsigned),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker de download automatico de PDFs assinados da MidiaSimples.")
    parser.add_argument("--interval", type=int, default=3600, help="Intervalo em segundos entre verificacoes.")
    parser.add_argument("--once", action="store_true", help="Executa um ciclo e encerra.")
    parser.add_argument("--technician", default=None, help="Tecnico/responsavel filtrado. Mantido por compatibilidade.")
    parser.add_argument("--technicians", nargs="+", default=list(DEFAULT_TECHNICIANS), help="Lista de tecnicos/responsaveis filtrados.")
    parser.add_argument("--batch-limit", type=int, default=20, help="Maximo de novos downloads por ciclo. Use 0 para baixar todos os novos do ciclo.")
    parser.add_argument("--max-pages", type=int, default=10, help="Maximo de paginas consultadas por tipo.")
    parser.add_argument("--page-size", type=int, default=100, help="Registros consultados por pagina.")
    parser.add_argument("--email", default=settings.midiasimples_email, help="E-mail da sessao MidiaSimples.")
    parser.add_argument("--output-root", default="data/downloads/signed_pdfs", help="Pasta raiz dos PDFs.")
    parser.add_argument("--state-file", default="data/runtime/download_worker_state.json", help="Arquivo de controle de documentos ja baixados.")
    parser.add_argument("--log-file", default="data/runtime/logs/download_worker.log", help="Arquivo de log do worker.")
    parser.add_argument(
        "--types",
        nargs="+",
        default=["rat", "laudo", "concessao", "emprestimo", "devolucao"],
        choices=["rat", "laudo", "concessao", "emprestimo", "devolucao"],
    )
    parser.add_argument("--unsigned-report-file", default="data/runtime/logs/download_worker_unsigned_report.csv", help="Relatorio CSV dos documentos ainda nao assinados.")
    args = parser.parse_args()

    configure_logging(args.log_file)
    logging.info(
        "\n"
        "============================================================\n"
        "WORKER DOWNLOAD PDFS ASSINADOS - MD HUB CENTRAL\n"
        "============================================================\n"
        "Status: iniciado\n"
        "Tecnico: %s\n"
        "Verificacao: a cada %s\n"
        "Downloads por ciclo: %s\n"
        "Tipos: %s\n"
        "Pasta de saida: %s\n"
        "Log: %s\n"
        "Estado: %s\n"
        "Relatorio nao assinados: %s\n"
        "============================================================",
        ", ".join(args.technicians if args.technicians else [args.technician or ""]),
        _format_interval(max(args.interval, 5)),
        _format_batch_limit(args.batch_limit),
        ", ".join(args.types),
        Path(args.output_root).resolve(),
        Path(args.log_file).resolve(),
        Path(args.state_file).resolve(),
        Path(args.unsigned_report_file).resolve(),
    )

    while True:
        try:
            result = _run_download_cycle(
                email=str(args.email or "").strip(),
                technicians=args.technicians if args.technicians else [args.technician or ""],
                max_pages=args.max_pages,
                page_size=args.page_size,
                batch_limit=args.batch_limit,
                types=set(args.types),
                output_root=Path(args.output_root),
                state_path=Path(args.state_file),
                unsigned_report_path=Path(args.unsigned_report_file),
            )
            logging.info(
                "Resumo do ciclo: assinados=%s | novos=%s | selecionados=%s | baixados=%s | falhas=%s | nao_assinados=%s",
                result["candidates"],
                result["new"],
                result["selected"],
                result["downloaded"],
                result["failed"],
                result["unsigned"],
            )
        except KeyboardInterrupt:
            logging.info("Worker de download interrompido pelo operador.")
            return 0
        except Exception:
            logging.exception("Falha ao processar worker de download.")

        if args.once:
            return 0
        time.sleep(max(args.interval, 5))


if __name__ == "__main__":
    raise SystemExit(main())
