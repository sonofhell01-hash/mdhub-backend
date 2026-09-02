import threading
import time
import uuid

from sqlalchemy.orm import Session

from src.core.config import settings
from src.models.core import AIAuditEvent
from src.services.ai.errors import AIBusy, AIError
from src.services.ai.ollama_client import OllamaClient
from src.services.ai.safety import validate_output


_semaphore = threading.BoundedSemaphore(settings.ai_max_concurrent_requests)


def record_audit(db: Session, *, request_id: str, user_id: int, use_case: str, prompt_version: str,
                 input_chars: int, output_chars: int = 0, elapsed_ms: int = 0,
                 status: str, error_code: str | None = None, model: str | None = None) -> None:
    try:
        db.add(AIAuditEvent(
            id=str(uuid.uuid4()), request_id=request_id, user_id=user_id, use_case=use_case,
            model=model or settings.ollama_model, prompt_version=prompt_version, input_chars=input_chars,
            output_chars=output_chars, elapsed_ms=elapsed_ms, status=status, error_code=error_code,
        ))
        db.commit()
    except Exception:
        db.rollback()


def execute_ai_request(db: Session, *, user_id: int, use_case: str, input_chars: int,
                       messages: list[dict[str, str]], prompt_version: str) -> dict:
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    if not _semaphore.acquire(timeout=settings.ai_queue_wait_seconds):
        record_audit(db, request_id=request_id, user_id=user_id, use_case=use_case,
                     prompt_version=prompt_version, input_chars=input_chars, status="busy", error_code="busy")
        raise AIBusy("IA ocupada")
    try:
        suggestion = validate_output(OllamaClient().chat(messages))
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        record_audit(db, request_id=request_id, user_id=user_id, use_case=use_case,
                     prompt_version=prompt_version, input_chars=input_chars, output_chars=len(suggestion),
                     elapsed_ms=elapsed_ms, status="success")
        return {"request_id": request_id, "suggestion": suggestion,
                "warnings": ["Sugestao gerada por IA. Revise antes de usar."],
                "model": settings.ollama_model, "prompt_version": prompt_version, "elapsed_ms": elapsed_ms}
    except AIError as exc:
        record_audit(db, request_id=request_id, user_id=user_id, use_case=use_case,
                     prompt_version=prompt_version, input_chars=input_chars,
                     elapsed_ms=round((time.perf_counter() - started) * 1000),
                     status=exc.code if exc.code in {"timeout", "unavailable"} else "error", error_code=exc.code)
        raise
    except Exception:
        record_audit(db, request_id=request_id, user_id=user_id, use_case=use_case,
                     prompt_version=prompt_version, input_chars=input_chars,
                     elapsed_ms=round((time.perf_counter() - started) * 1000),
                     status="error", error_code="internal_error")
        raise
    finally:
        _semaphore.release()
