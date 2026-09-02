from typing import Any

import httpx

from src.core.config import settings
from src.services.ai.errors import AIInvalidResponse, AIRequestTimeout, AIUnavailable


class OllamaClient:
    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url
        self.timeout = httpx.Timeout(
            connect=settings.ollama_connect_timeout_seconds,
            read=settings.ollama_read_timeout_seconds,
            write=10.0,
            pool=settings.ollama_connect_timeout_seconds,
        )

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=self.timeout)
            response.raise_for_status()
            models = response.json().get("models", [])
        except httpx.TimeoutException as exc:
            raise AIRequestTimeout("Tempo limite ao consultar o Ollama") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AIUnavailable("Ollama local indisponivel") from exc
        names = {str(item.get("name", "")).split(":latest", 1)[0] for item in models}
        return {
            "reachable": True,
            "model_available": settings.ollama_model in names,
            "vision_model_available": settings.ollama_vision_model in names,
        }

    def chat(self, messages: list[dict[str, Any]], *, model: str | None = None,
             keep_alive: str | None = None, response_format: str | None = None,
             read_timeout_seconds: float | None = None, max_output_tokens: int | None = None,
             think: bool | None = None) -> str:
        payload = {
            "model": model or settings.ollama_model,
            "stream": False,
            "keep_alive": keep_alive if keep_alive is not None else settings.ollama_keep_alive,
            "messages": messages,
            "options": {
                "temperature": settings.ai_temperature,
                "num_predict": max_output_tokens or settings.ai_max_output_tokens,
            },
        }
        if response_format:
            payload["format"] = response_format
        if think is not None:
            payload["think"] = think
        try:
            timeout = self.timeout if read_timeout_seconds is None else httpx.Timeout(
                connect=settings.ollama_connect_timeout_seconds, read=read_timeout_seconds,
                write=10.0, pool=settings.ollama_connect_timeout_seconds,
            )
            response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise AIRequestTimeout("Tempo limite ao gerar a sugestao") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AIUnavailable("Ollama local indisponivel") from exc
        content = str((data.get("message") or {}).get("content") or "").strip()
        if not content:
            raise AIInvalidResponse("Ollama retornou uma resposta vazia")
        return content
