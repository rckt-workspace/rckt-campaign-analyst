"""OpenRouter chat-completions transport with one configurable model fallback.

Usage: a non-streaming response carries a "usage" object with prompt_tokens, completion_tokens, total_tokens and an
optional cost (see docs/PHASE2_PROVIDER_USAGE.md). Each HTTP call returns its own usage; nothing is inferred from
the text, and no extra request is made to read costs.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import httpx

from app.core.config import AISettings, get_ai_settings
from app.domain.usage import MAX_PROVIDER_COST, MAX_TOKEN_COUNT, ProviderUsage

logger = logging.getLogger(__name__)

RECOVERABLE = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}
PROVIDER_COST_SCALE = Decimal("0.00000001")  # NUMERIC(18, 8): exact amounts are rounded half-up to 8 decimals


class AIProviderError(Exception):
    """Safe upstream error identifier; never includes provider bodies or secrets.

    usage is set only when the provider answered and its usage was read, for example an empty answer that was charged.
    """

    usage: ProviderUsage | None = None


class RecoverableAIProviderError(AIProviderError):
    pass


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    used_fallback: bool
    usage: ProviderUsage | None = None


@dataclass(frozen=True)
class ModelAnswer:
    """The text of one call and the usage the provider reported for it. usage is None when it was absent or invalid."""

    text: str
    usage: ProviderUsage | None


def _token_count(value: object) -> int | None:
    # bool is an int subclass in Python, and a JSON 12.0 is not a count: both are invalid.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_TOKEN_COUNT else None


def _provider_cost(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = Decimal(value)
    if not isinstance(value, Decimal) or not value.is_finite():
        return None
    if value < 0 or value > MAX_PROVIDER_COST:
        return None
    return value.quantize(PROVIDER_COST_SCALE, rounding="ROUND_HALF_UP")


def parse_usage(raw: object) -> ProviderUsage | None:
    """Reads the usage object of one response. Absent or unusable fields stay None; zero is kept when reported.

    Returns None when no field is usable, so the attempt is recorded as "usage not reported". Malformed fields are
    logged by name only, never with their values.
    """
    if not isinstance(raw, dict):
        return None
    checked = (
        ("prompt_tokens", raw.get("prompt_tokens"), _token_count),
        ("completion_tokens", raw.get("completion_tokens"), _token_count),
        ("cost", raw.get("cost"), _provider_cost),
    )
    parsed: dict[str, Any] = {}
    malformed = []
    for name, value, reader in checked:
        parsed[name] = reader(value)
        if value is not None and parsed[name] is None:
            malformed.append(name)
    if malformed:
        logger.warning("provider usage has invalid fields: %s", ", ".join(malformed))
    usage = ProviderUsage(
        prompt_tokens=parsed["prompt_tokens"],
        completion_tokens=parsed["completion_tokens"],
        provider_cost=parsed["cost"],
    )
    return usage if usage.has_any_value else None


async def call_model_with_usage(
    settings: AISettings, model: str, messages: list[dict[str, str]],
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelAnswer:
    try:
        # httpx applies its timeout to each phase (connect, write, pool, read) separately. This deadline bounds
        # the whole call, so four calls can never exceed four times CHAT_TIMEOUT_MS plus overhead.
        async with asyncio.timeout(settings.timeout_seconds), httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds),
            transport=transport,
            follow_redirects=False,
        ) as client:
            response = await client.post(
                f"{settings.base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {settings.api_key}",
                    "X-Title": "RCKT Campaign Analyst",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": settings.temperature,
                    "top_p": settings.top_p,
                    "max_tokens": settings.max_tokens,
                    "stream": False,
                },
            )
    except TimeoutError as exc:
        logger.warning("LLM total deadline exceeded on model %s", model)
        raise RecoverableAIProviderError("AI_TIMEOUT") from exc
    except httpx.TimeoutException as exc:
        logger.warning("LLM timeout on model %s", model)
        raise RecoverableAIProviderError("AI_TIMEOUT") from exc
    except httpx.RequestError as exc:
        logger.warning("LLM transport failure on model %s", model)
        raise RecoverableAIProviderError("AI_NETWORK") from exc

    if not response.is_success:
        logger.warning("LLM returned HTTP %d for model %s", response.status_code, model)
        invalid_model = (
            response.status_code == 404
            or (
                response.status_code == 400
                and any(token in response.text[:400].lower() for token in ("valid model", "not found", "unavailable"))
            )
        )
        error_class = RecoverableAIProviderError if response.status_code in RECOVERABLE or invalid_model else AIProviderError
        raise error_class(f"AI_{response.status_code}")

    # Decimal for floats: the cost is kept exactly as the provider wrote it, never rounded through a binary float.
    try:
        payload: Any = json.loads(response.text, parse_float=Decimal)
    except ValueError as exc:
        logger.warning("LLM response is not JSON for model %s", model)
        raise RecoverableAIProviderError("AI_EMPTY") from exc
    usage = parse_usage(payload.get("usage")) if isinstance(payload, dict) else None
    try:
        choice = payload["choices"][0]
        text = choice["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty content")
        if choice.get("finish_reason") == "length":
            # The report was cut at CHAT_MAX_TOKENS; the answer is still returned so the format guard can decide.
            logger.warning("LLM response truncated by max_tokens for model %s", model)
        return ModelAnswer(text.strip(), usage)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        logger.warning("LLM empty or invalid response for model %s", model)
        error = RecoverableAIProviderError("AI_EMPTY")
        error.usage = usage  # the provider answered, so this call may have been charged
        raise error from exc


async def call_model(
    settings: AISettings, model: str, messages: list[dict[str, str]],
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """The text only. Kept for callers that do not record usage."""
    return (await call_model_with_usage(settings, model, messages, transport=transport)).text


class CallObserver(Protocol):
    """Receives one event before and one after every HTTP call. Optional: without it, nothing changes."""

    async def started(self, model_role: str, model: str) -> object:
        """Called before the request is sent. Returns a token passed back to finished()."""

    async def finished(
        self, token: object, *, error_code: str | None, http_status: int | None, usage: ProviderUsage | None = None,
    ) -> None:
        """Called once with the outcome. error_code is None on success; usage is None when not reported."""


SAFE_PROVIDER_CODES = ("AI_TIMEOUT", "AI_NETWORK", "AI_EMPTY")
_HTTP_ERROR = re.compile(r"^AI_(\d{3})$")


def classify_provider_error(message: str) -> tuple[str, int | None]:
    """Maps an internal provider error identifier to a stored safe code and the HTTP status, if any.

    Provider bodies never reach this function: the message only carries AI_TIMEOUT, AI_NETWORK, AI_EMPTY or AI_<status>.
    """
    if message in SAFE_PROVIDER_CODES:
        return message, None
    match = _HTTP_ERROR.match(message)
    if match:
        return "AI_HTTP_ERROR", int(match.group(1))
    return "INTERNAL_ERROR", None


async def _observed_call(
    observer: CallObserver | None,
    model_role: str,
    settings: AISettings,
    model: str,
    messages: list[dict[str, str]],
    transport: httpx.AsyncBaseTransport | None,
) -> ModelAnswer:
    if observer is None:
        return await call_model_with_usage(settings, model, messages, transport=transport)
    token = await observer.started(model_role, model)
    try:
        answer = await call_model_with_usage(settings, model, messages, transport=transport)
    except AIProviderError as exc:
        code, status = classify_provider_error(str(exc))
        await observer.finished(token, error_code=code, http_status=status, usage=exc.usage)
        raise
    except Exception:
        # Unexpected failure: the call is still recorded, so no reserved row is left behind. The error is re-raised.
        await observer.finished(token, error_code="INTERNAL_ERROR", http_status=None, usage=None)
        raise
    await observer.finished(token, error_code=None, http_status=None, usage=answer.usage)
    return answer


async def run_chat_completion(
    messages: list[dict[str, str]],
    transport: httpx.AsyncBaseTransport | None = None,
    observer: CallObserver | None = None,
) -> ChatResult:
    settings = get_ai_settings()
    try:
        answer = await _observed_call(
            observer, "primary", settings, settings.primary_model, messages, transport
        )
        return ChatResult(answer.text, settings.primary_model, False, answer.usage)
    except RecoverableAIProviderError:
        if not settings.use_fallback or settings.primary_model == settings.fallback_model:
            raise
        logger.warning("LLM primary unavailable; attempting fallback")
        answer = await _observed_call(
            observer, "fallback", settings, settings.fallback_model, messages, transport
        )
        return ChatResult(answer.text, settings.fallback_model, True, answer.usage)
