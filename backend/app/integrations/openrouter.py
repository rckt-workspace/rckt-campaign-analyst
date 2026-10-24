"""OpenRouter chat-completions transport with one configurable model fallback."""

import logging
from dataclasses import dataclass
from typing import Any

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
        async with httpx.AsyncClient(
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
        text = payload["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty content")
        return text.strip()
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        logger.warning("LLM empty or invalid response for model %s", model)
        raise RecoverableAIProviderError("AI_EMPTY") from exc


async def run_chat_completion(
    messages: list[dict[str, str]],
    transport: httpx.AsyncBaseTransport | None = None,
) -> ChatResult:
    settings = get_ai_settings()
    try:
        answer = await call_model(settings, settings.primary_model, messages, transport=transport)
        return ChatResult(answer, settings.primary_model, False)
    except RecoverableAIProviderError:
        if not settings.use_fallback or settings.primary_model == settings.fallback_model:
            raise
        logger.warning("LLM primary unavailable; attempting fallback")
        answer = await call_model(settings, settings.fallback_model, messages, transport=transport)
        return ChatResult(answer, settings.fallback_model, True)
