"""Call observer: every real HTTP attempt is reported once, in order, with a safe code. Simulated transport only."""

import asyncio
import json

import httpx
import pytest

from app.integrations import openrouter
from app.integrations.openrouter import classify_provider_error, run_chat_completion
from app.services import analysis
from app.services.analysis import request_campaign_analysis
from app.domain.schemas import CampaignInput

VALID = "## Estado general\n🟡 Requiere atención\n## Resumen ejecutivo\nResumen."
MESSAGES = [{"role": "user", "content": "hola"}]


class Recorder:
    """Records started/finished events. Stands in for the persistence layer in these tests."""

    def __init__(self, step: str = "initial"):
        self.events: list[tuple] = []
        self.step = step

    async def started(self, model_role, model):  # type: ignore[no-untyped-def]
        self.events.append(("started", self.step, model_role, model))
        return len(self.events)

    async def finished(self, token, *, error_code, http_status, usage=None):  # type: ignore[no-untyped-def]
        self.events.append(("finished", error_code, http_status))


class StepRecorder:
    def __init__(self):  # type: ignore[no-untyped-def]
        self.recorder = Recorder()

    def for_step(self, step):  # type: ignore[no-untyped-def]
        self.recorder.step = step
        return self.recorder


@pytest.fixture(autouse=True)
def provider_env(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENROUTER_API_KEY", "unit-test-secret")
    monkeypatch.setenv("CHAT_PRIMARY_LLM", "primary-test")
    monkeypatch.setenv("CHAT_FALLBACK_LLM", "fallback-test")
    monkeypatch.setenv("CHAT_USE_FALLBACK", "true")


def script(replies):  # type: ignore[no-untyped-def]
    """Transport whose replies are consumed in order. An exception instance is raised instead of answering."""
    queue = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return httpx.MockTransport(handler)


def ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_a_successful_call_reports_start_and_success():
    recorder = Recorder()
    run(run_chat_completion(MESSAGES, transport=script([ok(VALID)]), observer=recorder))
    assert recorder.events == [("started", "initial", "primary", "primary-test"), ("finished", None, None)]


def test_timeout_then_fallback_is_reported_as_two_calls():
    recorder = Recorder()
    transport = script([httpx.ReadTimeout("slow"), ok(VALID)])
    result = run(run_chat_completion(MESSAGES, transport=transport, observer=recorder))
    assert result.used_fallback is True
    assert recorder.events == [
        ("started", "initial", "primary", "primary-test"),
        ("finished", "AI_TIMEOUT", None),
        ("started", "initial", "fallback", "fallback-test"),
        ("finished", None, None),
    ]


def test_recoverable_http_status_records_the_number_not_the_body():
    recorder = Recorder()
    transport = script([httpx.Response(429, text="secret provider body"), ok(VALID)])
    run(run_chat_completion(MESSAGES, transport=transport, observer=recorder))
    failed = recorder.events[1]
    assert failed == ("finished", "AI_HTTP_ERROR", 429)
    assert "secret" not in repr(recorder.events)


def test_a_non_recoverable_status_is_reported_and_never_falls_back():
    recorder = Recorder()
    transport = script([httpx.Response(401, text="bad key")])
    with pytest.raises(openrouter.AIProviderError):
        run(run_chat_completion(MESSAGES, transport=transport, observer=recorder))
    assert recorder.events == [("started", "initial", "primary", "primary-test"), ("finished", "AI_HTTP_ERROR", 401)]


def test_an_empty_answer_is_reported_as_ai_empty():
    recorder = Recorder()
    transport = script([httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]}), ok(VALID)])
    run(run_chat_completion(MESSAGES, transport=transport, observer=recorder))
    assert recorder.events[1] == ("finished", "AI_EMPTY", None)


def test_a_format_retry_is_reported_as_a_second_step(monkeypatch):
    recorder = StepRecorder()
    transport = script([ok("respuesta sin formato"), ok(VALID)])
    monkeypatch.setattr(analysis, "run_chat_completion",
                        lambda messages, **kwargs: run_chat_completion(messages, transport=transport, **kwargs))
    data = CampaignInput(rawInput="Campaña: Prueba\nInversión: 1000", campaign="Prueba", investment=1000)
    text = run(request_campaign_analysis(data, recorder=recorder))
    assert text.startswith("## Estado general")
    steps = [event[1] for event in recorder.recorder.events if event[0] == "started"]
    assert steps == ["initial", "format_retry"]


def test_without_an_observer_the_historical_call_is_unchanged():
    result = run(run_chat_completion(MESSAGES, transport=script([ok(VALID)])))
    assert result.used_fallback is False and result.text.startswith("## Estado general")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("AI_TIMEOUT", ("AI_TIMEOUT", None)),
        ("AI_NETWORK", ("AI_NETWORK", None)),
        ("AI_EMPTY", ("AI_EMPTY", None)),
        ("AI_502", ("AI_HTTP_ERROR", 502)),
        ("AI_404", ("AI_HTTP_ERROR", 404)),
        ("AI_TOTALLY_UNKNOWN", ("INTERNAL_ERROR", None)),
        ("secret body", ("INTERNAL_ERROR", None)),
    ],
)
def test_classification_only_produces_closed_codes(message, expected):
    assert classify_provider_error(message) == expected


def test_one_call_never_exceeds_its_total_deadline(monkeypatch):
    """A slow provider is cut off at CHAT_TIMEOUT_MS as a whole, not per httpx phase. Both steps time out in time."""
    import time

    monkeypatch.setenv("CHAT_TIMEOUT_MS", "1000")

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return ok(VALID)

    recorder = Recorder()
    started = time.monotonic()
    with pytest.raises(openrouter.AIProviderError):
        run(run_chat_completion(MESSAGES, transport=httpx.MockTransport(slow), observer=recorder))
    elapsed = time.monotonic() - started

    assert [event[1] for event in recorder.events if event[0] == "finished"] == ["AI_TIMEOUT", "AI_TIMEOUT"]
    assert elapsed < 4.0, f"two timed-out calls took {elapsed:.1f}s; the deadline is not enforced"
