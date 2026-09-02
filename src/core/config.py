import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _load_project_env() -> None:
    env_paths: list[Path] = [
        Path(__file__).resolve().parents[3] / ".env",
    ]

    explicit = os.getenv("MDHUB_SECRETS_FILE")
    if explicit:
        env_paths.append(Path(explicit))

    program_data = os.getenv("PROGRAMDATA")
    if program_data:
        env_paths.append(Path(program_data) / "MDHUB" / "secrets.env")

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        env_paths.append(Path(local_app_data) / "HUBREGIONAL" / "secrets.env")
        env_paths.append(Path(local_app_data) / "MD_HUB_FINAL" / "secrets.env")

    for env_path in env_paths:
        _load_env_file(env_path)


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _env_int_set(name: str) -> frozenset[int]:
    values: set[int] = set()
    for item in os.getenv(name, "").split(","):
        item = item.strip()
        if item.isdigit():
            values.add(int(item))
    return frozenset(values)



def _env_list(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


_load_project_env()


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    app_version: str
    server_host: str
    server_port: int
    secret_key: str
    access_token_expire_minutes: int
    database_url: str
    local_sqlite_path: str
    cors_origins: tuple[str, ...]
    is_serverless: bool
    midiasimples_base_url: str
    midiasimples_email: str
    midiasimples_password: str
    automatos_base_url: str
    automatos_id: str
    automatos_security_key: str
    sync_hub_url: str
    sync_ingest_token: str
    whatsapp_chrome_debug_port: int
    whatsapp_chrome_profile: str
    whatsapp_wait_initial_seconds: int
    whatsapp_send_wait_seconds: int
    whatsapp_between_messages_seconds: int
    whatsapp_batch_size: int
    whatsapp_max_attempts: int
    log_level: str
    ai_enabled: bool
    ai_pilot_enabled: bool
    ai_provider: str
    ollama_base_url: str
    ollama_model: str
    ollama_vision_model: str
    ollama_connect_timeout_seconds: float
    ollama_read_timeout_seconds: float
    ollama_keep_alive: str
    ollama_vision_keep_alive: str
    ollama_vision_read_timeout_seconds: float
    ai_max_input_chars: int
    ai_max_output_tokens: int
    ai_max_concurrent_requests: int
    ai_queue_wait_seconds: float
    ai_temperature: float
    ai_log_content: bool
    ai_allowed_user_ids: frozenset[int]
    ai_vision_max_images: int
    ai_vision_max_image_bytes: int
    ai_vision_max_total_bytes: int
    ai_vision_max_output_tokens: int


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name=_env("APP_NAME", "MD HUB FINAL"),
        app_env=_env("APP_ENV", "development"),
        app_version=_env("APP_VERSION", "0.1.0"),
        server_host=_env("SERVER_HOST", "0.0.0.0"),
        server_port=_env_int("SERVER_PORT", 8765),
        secret_key=_env("SECRET_KEY", "dev-only"),
        access_token_expire_minutes=_env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 720),
        database_url=_env("DATABASE_URL", "sqlite:///./data/runtime/mdhub_core.db"),
        local_sqlite_path=_env("LOCAL_SQLITE_PATH", "./data/runtime/clientes_rat.db"),
        cors_origins=_env_list("CORS_ORIGINS", "*"),
        is_serverless=_env_bool("VERCEL", False) or bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME")),
        midiasimples_base_url=_env("MIDIASIMPLES_BASE_URL", "https://api.arklok.midiasimples.com.br"),
        midiasimples_email=_env("MIDIASIMPLES_EMAIL", ""),
        midiasimples_password=_env("MIDIASIMPLES_PASSWORD", ""),
        automatos_base_url=_env("AUTOMATOS_BASE_URL", "https://lad2-smartcenter.almaden.app"),
        automatos_id=_env("AUTOMATOS_ID", ""),
        automatos_security_key=_env("AUTOMATOS_SECURITY_KEY", ""),
        sync_hub_url=_env("SYNC_HUB_URL", "http://10.136.59.60:8766"),
        sync_ingest_token=_env("SYNC_INGEST_TOKEN", ""),
        whatsapp_chrome_debug_port=_env_int("WHATSAPP_CHROME_DEBUG_PORT", 9222),
        whatsapp_chrome_profile=_env("WHATSAPP_CHROME_PROFILE", "./data/runtime/whatsapp_chrome_profile"),
        whatsapp_wait_initial_seconds=_env_int("WHATSAPP_WAIT_INITIAL_SECONDS", 60),
        whatsapp_send_wait_seconds=_env_int("WHATSAPP_SEND_WAIT_SECONDS", 4),
        whatsapp_between_messages_seconds=_env_int("WHATSAPP_BETWEEN_MESSAGES_SECONDS", 5),
        whatsapp_batch_size=_env_int("WHATSAPP_BATCH_SIZE", 10),
        whatsapp_max_attempts=_env_int("WHATSAPP_MAX_ATTEMPTS", 3),
        log_level=_env("LOG_LEVEL", "INFO"),
        ai_enabled=_env_bool("AI_ENABLED", False),
        ai_pilot_enabled=_env_bool("AI_PILOT_ENABLED", False),
        ai_provider=_env("AI_PROVIDER", "ollama"),
        ollama_base_url=_env("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=_env("OLLAMA_MODEL", "qwen3:4b-instruct"),
        ollama_vision_model=_env("OLLAMA_VISION_MODEL", "qwen2.5vl:3b"),
        ollama_connect_timeout_seconds=_env_float("OLLAMA_CONNECT_TIMEOUT_SECONDS", 3.0),
        ollama_read_timeout_seconds=_env_float("OLLAMA_READ_TIMEOUT_SECONDS", 120.0),
        ollama_keep_alive=_env("OLLAMA_KEEP_ALIVE", "5m"),
        ollama_vision_keep_alive=_env("OLLAMA_VISION_KEEP_ALIVE", "0"),
        ollama_vision_read_timeout_seconds=_env_float("OLLAMA_VISION_READ_TIMEOUT_SECONDS", 180.0),
        ai_max_input_chars=_env_int("AI_MAX_INPUT_CHARS", 6000),
        ai_max_output_tokens=_env_int("AI_MAX_OUTPUT_TOKENS", 800),
        ai_max_concurrent_requests=max(1, _env_int("AI_MAX_CONCURRENT_REQUESTS", 1)),
        ai_queue_wait_seconds=max(0.0, _env_float("AI_QUEUE_WAIT_SECONDS", 10.0)),
        ai_temperature=_env_float("AI_TEMPERATURE", 0.2),
        ai_log_content=_env_bool("AI_LOG_CONTENT", False),
        ai_allowed_user_ids=_env_int_set("AI_ALLOWED_USER_IDS"),
        ai_vision_max_images=max(1, _env_int("AI_VISION_MAX_IMAGES", 3)),
        ai_vision_max_image_bytes=max(1024, _env_int("AI_VISION_MAX_IMAGE_BYTES", 6 * 1024 * 1024)),
        ai_vision_max_total_bytes=max(1024, _env_int("AI_VISION_MAX_TOTAL_BYTES", 12 * 1024 * 1024)),
        ai_vision_max_output_tokens=max(128, _env_int("AI_VISION_MAX_OUTPUT_TOKENS", 450)),
    )


settings = get_settings()

if settings.app_env == "production" and settings.secret_key in ("", "dev-only"):
    raise RuntimeError(
        "SECRET_KEY precisa ser definido (variavel de ambiente) quando APP_ENV=production. "
        "Gere um valor com: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )
