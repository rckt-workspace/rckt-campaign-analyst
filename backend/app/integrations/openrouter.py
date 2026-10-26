"""OpenRouter chat-completions transport with one configurable model fallback."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import AISettings, get_ai_settings

logger = logging.getLogger(__name__)

RECOVERABLE = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}


class AIProviderError(Exception):
    """Safe upstream error identifier; never includes provider bodies or secrets."""


class RecoverableAIProviderError(AIProviderError):
    pass


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    used_fallback: bool


async def call_model(
    settings: AISettings, model: str, messages: list[dict[str, str]],
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
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

    try:
        payload: Any = response.json()
        choice = payload["choices"][0]
        text = choice["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty content")
        if choice.get("finish_reason") == "length":
            # The report was cut at CHAT_MAX_TOKENS; the answer is still returned so the format guard can decide.
            logger.warning("LLM response truncated by max_tokens for model %s", model)
        return text.strip()
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        logger.warning("LLM empty or invalid response for model %s", model)
        raise RecoverableAIProviderError("AI_EMPTY") from exc


class CallObserver(Protocol):
    """Receives one event before and one after every HTTP call. Optional: without it, nothing changes."""

    async def started(self, model_role: str, model: str) -> object:
        """Called before the request is sent. Returns a token passed back to finished()."""

    async def finished(self, token: object, *, error_code: str | None, http_status: int | None) -> None:
        """Called once with the outcome. error_code is None on success; http_status is None when unknown."""


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
) -> str:
    if observer is None:
        return await call_model(settings, model, messages, transport=transport)
    token = await observer.started(model_role, model)
    try:
        answer = await call_model(settings, model, messages, transport=transport)
    except AIProviderError as exc:
        code, status = classify_provider_error(str(exc))
        await observer.finished(token, error_code=code, http_status=status)
        raise
    except Exception:
        # Unexpected failure: the call is still recorded, so no reserved row is left behind. The error is re-raised.
        await observer.finished(token, error_code="INTERNAL_ERROR", http_status=None)
        raise
    await observer.finished(token, error_code=None, http_status=None)
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
        return ChatResult(answer, settings.primary_model, False)
    except RecoverableAIProviderError:
        if not settings.use_fallback or settings.primary_model == settings.fallback_model:
            raise
        logger.warning("LLM primary unavailable; attempting fallback")
        answer = await _observed_call(
            observer, "fallback", settings, settings.fallback_model, messages, transport
        )
        return ChatResult(answer, settings.fallback_model, True)
