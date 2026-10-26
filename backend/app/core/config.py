"""Configuration is read only by FastAPI; never forwarded to the browser."""

import os
from dataclasses import dataclass

# Verified against the OpenRouter model catalog (2026-10-24). See README "Modelos de IA".
# google/gemma-3-4b-it:free is no longer listed, so it was dropped as the default.
DEFAULT_PRIMARY_MODEL = "google/gemma-3-27b-it"
DEFAULT_FALLBACK_MODEL = "openrouter/free"
# A 250–450 word Spanish report with markdown needs roughly 700–900 tokens; 450 truncated it.
DEFAULT_MAX_TOKENS = 1200


@dataclass(frozen=True)
class AISettings:
    api_key: str
    base_url: str
    primary_model: str
    fallback_model: str
    use_fallback: bool
    temperature: float
    top_p: float
    max_tokens: int
    timeout_seconds: float


class AIConfigurationError(Exception):
    pass


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise AIConfigurationError(name) from exc
    if not minimum <= value <= maximum:
        raise AIConfigurationError(name)
    return value


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise AIConfigurationError(name) from exc
    if not minimum <= value <= maximum:
        raise AIConfigurationError(name)
    return value


def configured_model_names() -> tuple[str, str]:
    """Primary and fallback model identifiers, without requiring the API key. Recorded with each analysis."""
    primary = (os.environ.get("CHAT_PRIMARY_LLM") or DEFAULT_PRIMARY_MODEL).strip()
    fallback = (os.environ.get("CHAT_FALLBACK_LLM") or DEFAULT_FALLBACK_MODEL).strip()
    return primary, fallback


def get_ai_settings() -> AISettings:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise AIConfigurationError("OPENROUTER_API_KEY")
    base_url = (os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").strip().rstrip("/")
    primary = (os.environ.get("CHAT_PRIMARY_LLM") or DEFAULT_PRIMARY_MODEL).strip()
    fallback = (os.environ.get("CHAT_FALLBACK_LLM") or DEFAULT_FALLBACK_MODEL).strip()
    return AISettings(
        api_key=key,
        base_url=base_url,
        primary_model=primary,
        fallback_model=fallback,
        use_fallback=os.environ.get("CHAT_USE_FALLBACK", "true").lower().strip() in ("1", "true", "yes", "on"),
        temperature=_float_env("CHAT_TEMPERATURE", 0.2, 0, 2),
        top_p=_float_env("CHAT_TOP_P", 0.8, 0, 1),
        max_tokens=_int_env("CHAT_MAX_TOKENS", DEFAULT_MAX_TOKENS, 100, 8192),
        timeout_seconds=_int_env("CHAT_TIMEOUT_MS", 45000, 1000, 120000) / 1000,
    )


class DatabaseConfigurationError(Exception):
    """Invalid DATABASE_URL. The message never includes the URL or its password."""


@dataclass(frozen=True)
class DatabaseSettings:
    url: str
    pool_size: int
    max_overflow: int
    connect_timeout_seconds: float


DATABASE_URL_PREFIX = "postgresql+asyncpg://"


def get_database_settings(env_name: str = "DATABASE_URL") -> DatabaseSettings | None:
    """Return None when no database is configured, so the stateless MVP keeps starting unchanged."""
    url = os.environ.get(env_name, "").strip()
    if not url:
        return None
    if not url.startswith(DATABASE_URL_PREFIX):
        raise DatabaseConfigurationError(f"{env_name} must start with {DATABASE_URL_PREFIX}")
    try:
        return DatabaseSettings(
            url=url,
            pool_size=_int_env("DATABASE_POOL_SIZE", 5, 1, 50),
            max_overflow=_int_env("DATABASE_MAX_OVERFLOW", 5, 0, 50),
            connect_timeout_seconds=_int_env("DATABASE_CONNECT_TIMEOUT_SECONDS", 5, 1, 30),
        )
    except AIConfigurationError as exc:
        raise DatabaseConfigurationError(f"invalid database setting {exc}") from None


class AuthConfigurationError(Exception):
    """Invalid authentication settings. Never includes the values themselves."""


@dataclass(frozen=True)
class AuthSettings:
    issuer: str
    audience: str
    jwks_url: str


def get_auth_settings() -> AuthSettings | None:
    """Return None when SUPABASE_URL is unset. Protected endpoints then answer 503 (fail closed)."""
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not base:
        return None
    if not base.startswith("https://") and not base.startswith("http://127.0.0.1:") and not base.startswith("http://localhost:"):
        # Plain HTTP is accepted only for the local test issuer.
        raise AuthConfigurationError("SUPABASE_URL must use https")
    issuer = os.environ.get("SUPABASE_JWT_ISSUER", "").strip().rstrip("/") or f"{base}/auth/v1"
    audience = os.environ.get("SUPABASE_JWT_AUDIENCE", "").strip() or "authenticated"
    return AuthSettings(issuer=issuer, audience=audience, jwks_url=f"{base}/auth/v1/.well-known/jwks.json")
