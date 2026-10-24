import asyncio

import httpx
import pytest

from app.domain.metrics import analyze_metrics, build_deterministic_block, calculate_metrics
from app.domain.schemas import CampaignInput
from app.integrations.openrouter import AIProviderError, ChatResult, RecoverableAIProviderError, call_model
from app.core.config import AISettings
from app.services import analysis

VALID_REPORT = "## Estado general\n⚪ Información insuficiente\n## Resumen ejecutivo\nPrueba."


@pytest.fixture
def provider_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "unit-test-secret")
    monkeypatch.setenv("CHAT_PRIMARY_LLM", "primary-test")
    monkeypatch.setenv("CHAT_FALLBACK_LLM", "fallback-test")
    monkeypatch.setenv("CHAT_USE_FALLBACK", "true")


def settings(**overrides):
    base = dict(
        api_key="unit-test-secret", base_url="https://provider.test/api/v1",
        primary_model="primary-test", fallback_model="fallback-test", use_fallback=True,
        temperature=0.2, top_p=0.8, max_tokens=450, timeout_seconds=45,
    )
    base.update(overrides)
    return AISettings(**base)


def run(coro):
    return asyncio.run(coro)


def test_timeout_is_recoverable_and_safe():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow upstream", request=request)

    with pytest.raises(RecoverableAIProviderError, match="AI_TIMEOUT"):
        run(call_model(settings(), "primary-test", [], transport=httpx.MockTransport(timeout)))


def test_empty_provider_content_is_recoverable():
    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]})

    with pytest.raises(RecoverableAIProviderError, match="AI_EMPTY"):
        run(call_model(settings(), "primary-test", [], transport=httpx.MockTransport(empty)))


def test_fallback_disabled_does_not_call_second_model(monkeypatch, provider_env):
    from app.integrations import openrouter

    calls = []

    async def failing(settings_, model, messages, transport=None):
        calls.append(model)
        raise RecoverableAIProviderError("AI_503")

    monkeypatch.setenv("CHAT_USE_FALLBACK", "false")
    monkeypatch.setattr(openrouter, "call_model", failing)
    with pytest.raises(RecoverableAIProviderError):
        run(openrouter.run_chat_completion([{"role": "user", "content": "x"}]))
    assert calls == ["primary-test"]


def test_insufficient_information_is_reported_as_not_calculable():
    data = CampaignInput(rawInput="Sin datos numéricos", objective="Ventas")
    report = analyze_metrics(data)
    assert all(value is None for value in report.calculated.values())
    block = build_deterministic_block(report)
    assert "No calculable con los datos disponibles" in block
    assert any(w["type"] == "missing_critical_metric" for w in report.warnings)


def test_zero_leads_and_revenue_do_not_produce_infinite_values():
    data = CampaignInput(rawInput="x", investment=100, leads=0, revenue=0, clicks=0, impressions=0)
    calculated = calculate_metrics(data)
    assert calculated["cpl"] is None
    assert calculated["roas"] == 0
    assert calculated["ctr"] is None


def test_format_retry_replaces_unparseable_first_answer(monkeypatch):
    replies = iter(["Hola, aquí va el análisis sin encabezados.", VALID_REPORT])
    seen_messages = []

    async def fake_chat(messages):
        seen_messages.append(messages)
        return ChatResult(next(replies), "primary-test", False)

    monkeypatch.setattr(analysis, "run_chat_completion", fake_chat)
    result = run(analysis.request_campaign_analysis(CampaignInput(rawInput="x", investment=10)))
    assert result == VALID_REPORT
    assert len(seen_messages) == 2
    assert seen_messages[1][-1]["role"] == "user"
    assert "EXACTAMENTE" in seen_messages[1][-1]["content"]


def test_unparseable_retry_falls_back_to_first_answer(monkeypatch):
    replies = iter(["texto libre uno", "texto libre dos"])

    async def fake_chat(messages):
        return ChatResult(next(replies), "primary-test", False)

    monkeypatch.setattr(analysis, "run_chat_completion", fake_chat)
    result = run(analysis.request_campaign_analysis(CampaignInput(rawInput="x")))
    assert result == "texto libre uno"


def test_provider_failure_propagates_as_provider_error(monkeypatch):
    async def failing(messages):
        raise AIProviderError("AI_401")

    monkeypatch.setattr(analysis, "run_chat_completion", failing)
    with pytest.raises(AIProviderError, match="AI_401"):
        run(analysis.request_campaign_analysis(CampaignInput(rawInput="x")))
