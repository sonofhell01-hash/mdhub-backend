from __future__ import annotations

import argparse
import atexit
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from src.core.db_session import SessionLocal  # noqa: E402
from src.core.config import settings  # noqa: E402
from src.services.whatsapp.queue import send_pending_batch  # noqa: E402
from src.services.whatsapp.rat_reminders import block_old_pending_rat_reminders, sync_rat_signature_reminders  # noqa: E402
from src.services.whatsapp.web_sender import WhatsAppWebSender  # noqa: E402


_WORKER_LOCK_HANDLE = None


def _release_worker_lock() -> None:
    global _WORKER_LOCK_HANDLE
    if _WORKER_LOCK_HANDLE is None:
        return
    try:
        _WORKER_LOCK_HANDLE.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(_WORKER_LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(_WORKER_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    _WORKER_LOCK_HANDLE.close()
    _WORKER_LOCK_HANDLE = None


def _acquire_worker_lock() -> bool:
    global _WORKER_LOCK_HANDLE
    lock_path = SERVER_ROOT.parent / "data" / "runtime" / "whatsapp_worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    try:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        return False
    _WORKER_LOCK_HANDLE = handle
    atexit.register(_release_worker_lock)
    return True


def _format_interval(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}min"
    return f"{seconds}s"


def _configure_logging(log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
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


def _run_batch(limit: int, sender: WhatsAppWebSender) -> dict[str, Any]:
    db = SessionLocal()
    try:
        result = send_pending_batch(db, limit=limit, sender=sender)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _run_rat_sync(
    *,
    email: str,
    max_pages: int,
    page_size: int,
    queue_limit: int,
    reminder_cooldown_seconds: int,
    rat_created_from: str | None,
    force: bool,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        result = sync_rat_signature_reminders(
            db,
            email=email,
            max_pages=max_pages,
            page_size=page_size,
            queue_limit=queue_limit,
            reminder_cooldown_seconds=reminder_cooldown_seconds,
            rat_created_from=rat_created_from,
            force=force,
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _block_old_pending(*, rat_created_from: str | None) -> dict[str, Any]:
    db = SessionLocal()
    try:
        result = block_old_pending_rat_reminders(db, rat_created_from=rat_created_from)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Worker do HUB Central para envio de mensagens pendentes pelo WhatsApp Web."
    )
    parser.add_argument("--interval", type=int, default=60, help="Intervalo em segundos entre lotes.")
    parser.add_argument("--limit", type=int, default=10, help="Quantidade maxima por lote.")
    parser.add_argument("--once", action="store_true", help="Executa um unico lote e encerra.")
    parser.add_argument("--sync-rat", action="store_true", help="Consulta RATs pendentes no MidiaSimples antes de enviar.")
    parser.add_argument("--sync-rat-email", default=settings.midiasimples_email, help="E-mail da sessao MidiaSimples usada na consulta.")
    parser.add_argument("--sync-rat-interval", type=int, default=1800, help="Intervalo em segundos entre consultas de RATs.")
    parser.add_argument("--sync-rat-max-pages", type=int, default=30, help="Maximo de paginas de RATs por consulta.")
    parser.add_argument("--sync-rat-page-size", type=int, default=100, help="Quantidade de RATs por pagina consultada.")
    parser.add_argument("--sync-rat-queue-limit", type=int, default=200, help="Maximo de lembretes criados por consulta.")
    parser.add_argument("--reminder-cooldown", type=int, default=1800, help="Tempo minimo em segundos entre lembretes do mesmo documento.")
    parser.add_argument("--rat-created-from", default=None, help="Data minima da criacao da RAT para lembrete automatico, em YYYY-MM-DD.")
    parser.add_argument("--force", action="store_true", help="Forca enfileiramento mesmo quando a regra de bloqueio de cargo seria aplicada.")
    parser.add_argument("--log-file", default=None, help="Arquivo opcional para gravar o log do worker.")
    args = parser.parse_args()

    _configure_logging(args.log_file)
    if not _acquire_worker_lock():
        logging.warning("Outro worker WhatsApp ja esta ativo. Esta instancia sera encerrada.")
        return 0

    sender = WhatsAppWebSender()
    logging.info(
        "\n"
        "============================================================\n"
        "BOT WHATSAPP - MD HUB CENTRAL\n"
        "============================================================\n"
        "Status: iniciado\n"
        "Envio da fila: a cada %s\n"
        "Verificacao de assinatura: a cada %s\n"
        "Reenvio do lembrete: apos %s sem assinatura\n"
        "Data minima da RAT: %s\n"
        "Horario de envio: segunda a sexta, 08:00-18:00\n"
        "Mensagens por lote: %s\n"
        "============================================================",
        _format_interval(args.interval),
        _format_interval(args.sync_rat_interval) if args.sync_rat else "desativada",
        _format_interval(args.reminder_cooldown),
        args.rat_created_from or "sem corte de data",
        args.limit,
    )
    # Prioriza a fila ja existente na inicializacao. A varredura de PDFs pode
    # levar mais de um minuto e nao deve atrasar mensagens prontas para envio.
    next_sync_at = time.monotonic() + max(args.interval, 5)

    while True:
        try:
            now = time.monotonic()
            sync_email = str(args.sync_rat_email or "").strip()
            if args.sync_rat and now >= next_sync_at:
                next_sync_at = now + max(args.sync_rat_interval, 10)
                if sync_email:
                    sync_result = _run_rat_sync(
                        email=sync_email,
                        max_pages=args.sync_rat_max_pages,
                        page_size=args.sync_rat_page_size,
                        queue_limit=args.sync_rat_queue_limit,
                        reminder_cooldown_seconds=args.reminder_cooldown,
                        rat_created_from=args.rat_created_from,
                        force=args.force,
                    )
                    for item in sync_result.get("items", []):
                        logging.info(
                            "Documento nao assinado encontrado - %s %s - Mensagem adicionada na fila.",
                            item.get("rat_id") or item.get("numero_chamado") or item.get("document_id"),
                            item.get("nome") or "Colaborador",
                        )
                    logging.info(
                        "\n"
                        "Verificacao de assinaturas concluida\n"
                        "Documentos analisados: %s\n"
                        "Documentos aptos para lembrete: %s\n"
                        "Mensagens criadas agora: %s\n"
                        "Ja assinadas: %s\n"
                        "Fora dos tecnicos permitidos: %s\n"
                        "Antigas ignoradas: %s\n"
                        "Aguardando 1h da criacao: %s\n"
                        "Aguardando assinatura do tecnico: %s\n"
                        "Aguardando prazo de reenvio: %s\n"
                        "Ignoradas por regra/duplicidade: %s\n"
                        "Data minima considerada: %s",
                        sync_result.get("scanned", 0),
                        sync_result.get("eligible", 0),
                        sync_result.get("queued", 0),
                        sync_result.get("signed", 0),
                        sync_result.get("out_of_scope", 0),
                        sync_result.get("out_of_date", 0),
                        sync_result.get("waiting_min_age", 0),
                        sync_result.get("waiting_technician_signature", 0),
                        sync_result.get("waiting_cooldown", 0),
                        sync_result.get("skipped", 0),
                        args.rat_created_from or "sem corte",
                    )
                else:
                    logging.warning("Verificacao de RAT habilitada, mas o e-mail do MidiaSimples nao foi informado.")

            if args.rat_created_from:
                blocked_old = _block_old_pending(rat_created_from=args.rat_created_from)
                if blocked_old.get("blocked", 0):
                    logging.info(
                        "Documentos antigos bloqueados na fila: %s | data minima: %s",
                        blocked_old.get("blocked", 0),
                        blocked_old.get("cutoff"),
                    )

            result = _run_batch(args.limit, sender)
            if result.get("deferred"):
                logging.info("Fila aguardando: %s.", result.get("reason"))
            for item in result.get("items", []):
                if item.get("status") in ("enviado", "encaminhado_hub"):
                    logging.info(
                        "Documento nao assinado encontrado - %s %s - Mensagem enviada.",
                        item.get("documento_numero") or item.get("documento_id") or item.get("id"),
                        item.get("nome") or "Colaborador",
                    )
                elif item.get("status") == "falha":
                    logging.warning(
                        "Documento nao assinado encontrado - %s %s - Falha ao enviar: %s",
                        item.get("documento_numero") or item.get("documento_id") or item.get("id"),
                        item.get("nome") or "Colaborador",
                        item.get("erro") or "erro desconhecido",
                    )
            if result.get("processed", 0) or result.get("sent", 0) or result.get("failed", 0):
                logging.info(
                    "Resumo do lote: processadas=%s | enviadas=%s | falhas=%s",
                    result.get("processed", 0),
                    result.get("sent", 0),
                    result.get("failed", 0),
                )
        except KeyboardInterrupt:
            logging.info("Worker interrompido pelo operador.")
            return 0
        except Exception:
            logging.exception("Falha ao processar o bot WhatsApp.")

        if args.once:
            return 0
        time.sleep(max(args.interval, 5))


if __name__ == "__main__":
    raise SystemExit(main())
