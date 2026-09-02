import base64
import io
import json
import re
import threading
import time
import uuid
from typing import Any

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from src.core.config import settings
from src.services.ai.ai_service import record_audit
from src.services.ai.errors import AIBusy, AIInputRejected, AIError
from src.services.ai.ollama_client import OllamaClient


_vision_semaphore = threading.BoundedSemaphore(1)
_DATA_URL = re.compile(r"^data:(image/(?:jpeg|png|webp));base64,(.+)$", re.IGNORECASE | re.DOTALL)
_FIELDS = (
    "visible_damage", "damage_location", "possible_misuse_indicators", "limitations",
)


def _prepare_image(data_url: str) -> tuple[str, int]:
    match = _DATA_URL.match(data_url.strip())
    if not match:
        raise AIInputRejected("Somente imagens JPEG, PNG ou WebP podem ser analisadas")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (ValueError, TypeError) as exc:
        raise AIInputRejected("Imagem em formato invalido") from exc
    if len(raw) > settings.ai_vision_max_image_bytes:
        raise AIInputRejected("Uma das imagens excede o limite para analise")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image = image.convert("RGB")
            image.thumbnail((640, 640))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=82, optimize=True)
            prepared = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AIInputRejected("Arquivo de imagem corrompido ou invalido") from exc
    return base64.b64encode(prepared).decode("ascii"), len(raw)


def _messages(images: list[str], template_label: str, observation: str) -> list[dict[str, Any]]:
    context = []
    if template_label.strip():
        context.append(f"Template selecionado pelo tecnico: {template_label.strip()}")
    if observation.strip():
        context.append(f"Observacao fornecida pelo tecnico: {observation.strip()}")
    context_text = "\n".join(context) or "Nenhum contexto adicional foi fornecido."
    system = (
        "Voce auxilia um tecnico de suporte a documentar evidencias fotograficas de equipamentos. "
        "Descreva somente o que estiver visivel nas imagens. Nao invente defeitos internos, causa, impacto ou reparo. "
        "Nunca declare uso inadequado como conclusao: registre apenas indicios visuais possiveis, que exigem confirmacao humana. "
        "Se a imagem nao permitir uma conclusao, declare a limitacao. Produza todos os valores em portugues brasileiro formal e objetivo."
    )
    prompt = f"""Analise as imagens anexadas ao laudo.
{context_text}

Retorne exclusivamente JSON valido com esta estrutura:
{{
  "visible_damage": ["observacoes visiveis objetivas"],
  "damage_location": ["localizacao visual do dano"],
  "possible_misuse_indicators": ["indicios possiveis, sem emitir veredito"],
  "limitations": ["o que nao pode ser confirmado pelas imagens"]
}}
Use no maximo dois itens curtos por lista. Use listas vazias quando nao houver evidencias. Nao inclua markdown."""
    return [{"role": "system", "content": system}, {"role": "user", "content": prompt, "images": images}]


def _parse(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
    except json.JSONDecodeError:
        value = {"limitations": ["A resposta visual não pôde ser estruturada; revise as imagens manualmente."]}
    if not isinstance(value, dict):
        value = {"limitations": ["A resposta visual não pôde ser estruturada; revise as imagens manualmente."]}
    result: dict[str, Any] = {}
    for field in _FIELDS:
        items = value.get(field, [])
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            items = []
        result[field] = [item.strip()[:800] for item in items[:10] if item.strip()]
    if not any(result[field] for field in _FIELDS):
        result["limitations"] = ["As imagens não apresentaram elementos suficientes para uma descrição conclusiva."]
    damages = "; ".join(result["visible_damage"])
    locations = "; ".join(result["damage_location"])
    limitations = "; ".join(result["limitations"])
    result["suggested_actions"] = "Foi realizada inspeção visual das imagens anexadas ao laudo, com registro das evidências observáveis."
    result["suggested_defect"] = (f"Durante a inspeção visual, foram observados: {damages}."
                                  if damages else "Não foram identificados danos visíveis conclusivos nas imagens analisadas.")
    location_text = f" Localização aparente: {locations}." if locations else ""
    limitation_text = f" Limitações da análise visual: {limitations}." if limitations else ""
    result["suggested_analysis"] = (f"As imagens apresentam as evidências visuais descritas.{location_text}"
                                    f" A confirmação da causa e da extensão do dano exige avaliação técnica presencial.{limitation_text}")
    result["suggested_solution"] = "Recomenda-se avaliação técnica presencial para confirmar o diagnóstico e definir o reparo ou a substituição aplicável."
    return result


def analyze_laudo_images(db: Session, *, user_id: int, image_data_urls: list[str],
                         template_label: str, observation: str) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    version = "laudo-visual-analysis-v1"
    started = time.perf_counter()
    if len(image_data_urls) > settings.ai_vision_max_images:
        raise AIInputRejected(f"Envie no maximo {settings.ai_vision_max_images} imagens por analise")
    prepared: list[str] = []
    total_bytes = 0
    for data_url in image_data_urls:
        image, original_size = _prepare_image(data_url)
        prepared.append(image)
        total_bytes += original_size
    if total_bytes > settings.ai_vision_max_total_bytes:
        raise AIInputRejected("O conjunto de imagens excede o limite para analise")
    if not _vision_semaphore.acquire(timeout=settings.ai_queue_wait_seconds):
        record_audit(db, request_id=request_id, user_id=user_id, use_case="analyze_laudo_images",
                     prompt_version=version, input_chars=total_bytes, status="busy", error_code="busy",
                     model=settings.ollama_vision_model)
        raise AIBusy("IA visual ocupada")
    try:
        content = OllamaClient().chat(
            _messages(prepared, template_label, observation), model=settings.ollama_vision_model,
            keep_alive=settings.ollama_vision_keep_alive, response_format="json",
            read_timeout_seconds=settings.ollama_vision_read_timeout_seconds,
            max_output_tokens=settings.ai_vision_max_output_tokens,
            think=False,
        )
        analysis = _parse(content)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        output_chars = len(json.dumps(analysis, ensure_ascii=False))
        record_audit(db, request_id=request_id, user_id=user_id, use_case="analyze_laudo_images",
                     prompt_version=version, input_chars=total_bytes, output_chars=output_chars,
                     elapsed_ms=elapsed_ms, status="success", model=settings.ollama_vision_model)
        return {"request_id": request_id, "analysis": analysis,
                "warnings": ["Analise visual gerada por IA. Confirme as evidencias antes de aplicar.",
                             "A IA nao altera a marcacao de uso inadequado."],
                "model": settings.ollama_vision_model, "prompt_version": version, "elapsed_ms": elapsed_ms}
    except AIError as exc:
        record_audit(db, request_id=request_id, user_id=user_id, use_case="analyze_laudo_images",
                     prompt_version=version, input_chars=total_bytes,
                     elapsed_ms=round((time.perf_counter() - started) * 1000), status="error",
                     error_code=exc.code, model=settings.ollama_vision_model)
        raise
    finally:
        _vision_semaphore.release()
