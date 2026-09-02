import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.core.auth import get_current_user
from src.core.config import settings
from src.core.db_session import get_db
from src.models.core import User
from src.schemas.ai import AIHealthResponse, AIResponse, AnalyzeLaudoImagesRequest, LaudoVisualAnalysisResponse, ReplacementScriptRequest, ReviewReportRequest, SummarizeRequest
from src.services.ai.ai_service import execute_ai_request, record_audit
from src.services.ai.errors import AIBusy, AIInputRejected, AIInputTooLarge, AIInvalidResponse, AIRequestTimeout, AIUnavailable
from src.services.ai.ollama_client import OllamaClient
from src.services.ai.prompt_builder import replacement_script, review_report, summarize
from src.services.ai.safety import validate_input
from src.services.ai.vision_service import analyze_laudo_images


router = APIRouter(prefix="/ai", tags=["IA local - piloto"])


def _require_pilot(user: User) -> None:
    if not settings.ai_enabled or not settings.ai_pilot_enabled:
        raise HTTPException(status_code=503, detail="Assistente temporariamente indisponivel. Continue o preenchimento normalmente.")
    if user.id not in settings.ai_allowed_user_ids:
        raise HTTPException(status_code=403, detail="Usuario fora do piloto de IA")


def _raise_ai_error(exc: Exception) -> None:
    if isinstance(exc, AIBusy):
        raise HTTPException(status_code=409, detail="IA ocupada; tente novamente") from exc
    if isinstance(exc, AIRequestTimeout):
        raise HTTPException(status_code=504, detail="Tempo limite do assistente excedido") from exc
    if isinstance(exc, (AIUnavailable, AIInvalidResponse)):
        raise HTTPException(status_code=503, detail="Assistente temporariamente indisponivel") from exc
    raise exc


def _validate_or_reject(db: Session, user: User, use_case: str, prompt_version: str, text: str) -> str:
    try:
        return validate_input(text)
    except (AIInputRejected, AIInputTooLarge) as exc:
        record_audit(db, request_id=str(uuid.uuid4()), user_id=user.id, use_case=use_case,
                     prompt_version=prompt_version, input_chars=len(str(text or "")),
                     status="rejected", error_code=exc.code)
        raise HTTPException(status_code=413 if isinstance(exc, AIInputTooLarge) else 400, detail=str(exc)) from exc


@router.get("/health", response_model=AIHealthResponse)
def ai_health(user: User = Depends(get_current_user)):
    reachable = model_available = vision_model_available = False
    try:
        result = OllamaClient().health()
        reachable, model_available = bool(result["reachable"]), bool(result["model_available"])
        vision_model_available = bool(result.get("vision_model_available", False))
    except (AIUnavailable, AIRequestTimeout):
        pass
    allowed = settings.ai_enabled and settings.ai_pilot_enabled and user.id in settings.ai_allowed_user_ids
    return AIHealthResponse(enabled=settings.ai_enabled, pilot=settings.ai_pilot_enabled, allowed=allowed,
                            provider=settings.ai_provider, reachable=reachable,
                            model=settings.ollama_model, model_available=model_available,
                            vision_model=getattr(settings, "ollama_vision_model", "qwen2.5vl:3b"),
                            vision_model_available=vision_model_available)


@router.post("/analyze-laudo-images", response_model=LaudoVisualAnalysisResponse)
def analyze_laudo_images_endpoint(body: AnalyzeLaudoImagesRequest, user: User = Depends(get_current_user),
                                  db: Session = Depends(get_db)):
    _require_pilot(user)
    try:
        return analyze_laudo_images(db, user_id=user.id, image_data_urls=[image.data_url for image in body.images],
                                    template_label=body.template_label,
                                    observation=body.technician_observation)
    except (AIInputRejected, AIInputTooLarge) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (AIBusy, AIRequestTimeout, AIUnavailable, AIInvalidResponse) as exc:
        _raise_ai_error(exc)


@router.post("/review-report", response_model=AIResponse)
def review_report_endpoint(body: ReviewReportRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pilot(user)
    text = _validate_or_reject(db, user, "review_report", "review-report-v1", body.text)
    messages, version = review_report(text, body.context.document_type, body.context.usage_inappropriate_selected)
    try:
        return execute_ai_request(db, user_id=user.id, use_case="review_report", input_chars=len(text), messages=messages, prompt_version=version)
    except (AIBusy, AIRequestTimeout, AIUnavailable, AIInvalidResponse) as exc:
        _raise_ai_error(exc)


@router.post("/summarize", response_model=AIResponse)
def summarize_endpoint(body: SummarizeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pilot(user)
    text = _validate_or_reject(db, user, "summarize", "operational-summary-v1", body.text)
    messages, version = summarize(text)
    try:
        return execute_ai_request(db, user_id=user.id, use_case="summarize", input_chars=len(text), messages=messages, prompt_version=version)
    except (AIBusy, AIRequestTimeout, AIUnavailable, AIInvalidResponse) as exc:
        _raise_ai_error(exc)


@router.post("/suggest-replacement-script", response_model=AIResponse)
def replacement_endpoint(body: ReplacementScriptRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pilot(user)
    replacement_type = _validate_or_reject(db, user, "replacement_script", "replacement-script-v1", body.replacement_type)
    facts = [_validate_or_reject(db, user, "replacement_script", "replacement-script-v1", fact) for fact in body.known_facts]
    combined_chars = len(replacement_type) + sum(len(fact) for fact in facts)
    if combined_chars > settings.ai_max_input_chars:
        raise HTTPException(status_code=413, detail="Texto acima do limite do piloto")
    messages, version = replacement_script(replacement_type, facts)
    try:
        return execute_ai_request(db, user_id=user.id, use_case="replacement_script", input_chars=combined_chars, messages=messages, prompt_version=version)
    except (AIBusy, AIRequestTimeout, AIUnavailable, AIInvalidResponse) as exc:
        _raise_ai_error(exc)
