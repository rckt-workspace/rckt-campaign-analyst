"""Provider usage per HTTP call: parsing, exact money, persistence per attempt, and unknown outcomes.

Every provider here is simulated (httpx.MockTransport or the analysis test provider). No test reaches OpenRouter.
Contract and decisions: docs/PHASE2_PROVIDER_USAGE.md.
"""

import asyncio
import json
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import AISettings
from app.db import analysis_ledger as ledger
from app.domain.usage import MAX_PROVIDER_COST, ProviderUsage, usd_to_micros
from app.integrations.openrouter import (
    RecoverableAIProviderError,
    call_model_with_usage,
    classify_provider_error,
    parse_usage,
)
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import make_token
from tests.conftest import run
from tests.test_analysis_api import BODY, INVALID, VALID, owner, post, provider  # noqa: F401  (pytest fixtures)
from tests.test_analysis_ledger import PRIMARY, REPORT, rows
from tests.test_campaign_api import seed

HASH = "e" * 64


def _body(text_value: str, usage_json: str | None) -> bytes:
    """A raw response body. The usage is written as JSON text so the exact number reaches the parser."""
    payload = '{"choices":[{"message":{"content":' + json.dumps(text_value) + '}}]'
    if usage_json is not None:
        payload += ',"usage":' + usage_json
    return (payload + "}").encode("utf-8")


def reply(text_value: str, usage_json: str | None = None) -> httpx.Response:
    return httpx.Response(200, content=_body(text_value, usage_json))


def settings_for_unit() -> AISettings:
    return AISettings(
        api_key="unit-test-secret", base_url="https://openrouter.ai/api/v1", primary_model="p",
        fallback_model="f", use_fallback=True, temperature=0.2, top_p=0.8, max_tokens=1200, timeout_seconds=5,
    )


# --- parsing: absent, partial, explicit zero, malformed -----------------------------------------------------------

def test_no_usage_object_means_not_reported():
    assert parse_usage(None) is None
    assert parse_usage("usage") is None
    assert parse_usage({}) is None


def test_only_tokens_leave_the_cost_unreported():
    usage = parse_usage({"prompt_tokens": 812, "completion_tokens": 430, "total_tokens": 1242})
    assert usage == ProviderUsage(prompt_tokens=812, completion_tokens=430, provider_cost=None, cost_micros=None)


def test_only_cost_leaves_the_tokens_unreported():
    usage = parse_usage({"cost": Decimal("0.00012345")})
    assert usage.prompt_tokens is None and usage.completion_tokens is None
    assert usage.provider_cost == Decimal("0.00012345")


def test_an_explicit_zero_cost_is_kept_as_zero_not_as_missing():
    usage = parse_usage({"cost": 0, "prompt_tokens": 10})
    assert usage.provider_cost == Decimal("0")
    assert usage.provider_cost is not None


@pytest.mark.parametrize(
    "field, value",
    [
        ("prompt_tokens", "812"),            # a string is not a count
        ("prompt_tokens", True),             # bool is an int in Python; it is not a count
        ("completion_tokens", Decimal("1.0")),  # a fractional token count is invalid
        ("completion_tokens", -1),
        ("prompt_tokens", 2_147_483_648),    # above the INTEGER column
        ("cost", "0.5"),                     # a string amount is not a number
        ("cost", float("nan")),              # NaN is never an amount
        ("cost", Decimal("-0.01")),
        ("cost", MAX_PROVIDER_COST + Decimal("1")),
    ],
)
def test_a_malformed_field_is_dropped_without_affecting_the_others(field, value):
    payload = {"prompt_tokens": 7, "completion_tokens": 3, "cost": Decimal("0.5")}
    payload[field] = value  # exactly one field is invalid; the other two stay valid
    usage = parse_usage(payload)
    attribute = {"cost": "provider_cost"}.get(field, field)
    assert getattr(usage, attribute) is None
    assert [v for k, v in (("prompt_tokens", usage.prompt_tokens), ("completion_tokens", usage.completion_tokens),
                           ("provider_cost", usage.provider_cost)) if k != attribute] == [
        v for k, v in (("prompt_tokens", 7), ("completion_tokens", 3), ("provider_cost", Decimal("0.5")))
        if k != attribute
    ]


def test_a_usage_object_with_only_invalid_fields_is_not_reported():
    assert parse_usage({"prompt_tokens": "x", "cost": "y"}) is None


def test_the_cost_is_rounded_half_up_to_eight_decimals_explicitly():
    assert parse_usage({"cost": Decimal("0.123456789")}).provider_cost == Decimal("0.12345679")
    assert parse_usage({"cost": Decimal("0.123456784")}).provider_cost == Decimal("0.12345678")


# --- money: exact conversion to micro-USD ------------------------------------------------------------------------

@pytest.mark.parametrize(
    "amount, micros",
    [
        ("0", 0),
        ("0.0000004", 0),       # 0.4 micro rounds down
        ("0.0000005", 1),       # 0.5 micro rounds half up
        ("0.00000149", 1),
        ("1.2345678", 1_234_568),
        ("0.00012345", 123),
        ("9999999999.99999999", 10_000_000_000_000_000),  # the largest amount: 9999999999.99999999 -> 10^16 micros
    ],
)
def test_usd_to_micros_is_exact_and_rounds_half_up(amount, micros):
    assert usd_to_micros(Decimal(amount)) == micros


@pytest.mark.parametrize("amount", [Decimal("-0.000001"), Decimal("NaN"), Decimal("Infinity"), Decimal("10000000000")])
def test_usd_to_micros_refuses_negative_non_finite_and_out_of_range(amount):
    with pytest.raises(ValueError):
        usd_to_micros(amount)


def test_usd_to_micros_refuses_a_float_so_no_binary_rounding_can_enter():
    with pytest.raises(ValueError):
        usd_to_micros(0.1)  # type: ignore[arg-type]


# --- transport: usage of each call, and of calls that failed after an answer ------------------------------------

def test_a_successful_call_returns_its_exact_usage():
    transport = httpx.MockTransport(lambda request: reply(VALID, '{"prompt_tokens":812,"completion_tokens":430,"cost":0.0001234}'))
    answer = run(call_model_with_usage(settings_for_unit(), "p", [], transport=transport))
    assert answer.text.startswith("## Estado general")
    assert answer.usage.prompt_tokens == 812 and answer.usage.completion_tokens == 430
    assert answer.usage.provider_cost == Decimal("0.0001234")


def test_a_call_without_usage_returns_none_and_still_answers():
    transport = httpx.MockTransport(lambda request: reply(VALID))
    answer = run(call_model_with_usage(settings_for_unit(), "p", [], transport=transport))
    assert answer.usage is None and answer.text


def test_an_empty_answer_that_was_charged_keeps_its_usage_on_the_error():
    transport = httpx.MockTransport(lambda request: reply("   ", '{"prompt_tokens":50,"cost":0.003}'))
    with pytest.raises(RecoverableAIProviderError) as raised:
        run(call_model_with_usage(settings_for_unit(), "p", [], transport=transport))
    assert str(raised.value) == "AI_EMPTY"
    assert raised.value.usage == ProviderUsage(prompt_tokens=50, provider_cost=Decimal("0.003"))


def test_a_timeout_has_no_usage_because_the_outcome_is_unknown():
    def slow(request):  # type: ignore[no-untyped-def]
        raise httpx.ReadTimeout("simulated", request=request)

    with pytest.raises(RecoverableAIProviderError) as raised:
        run(call_model_with_usage(settings_for_unit(), "p", [], transport=httpx.MockTransport(slow)))
    assert classify_provider_error(str(raised.value)) == ("AI_TIMEOUT", None)
    assert raised.value.usage is None


# --- persistence: one row per HTTP call, with the usage of that call ---------------------------------------------

def usage_rows(raw_engine, analysis_id):  # type: ignore[no-untyped-def]
    return rows(
        raw_engine,
        "SELECT attempt_number, step, model_role, status, error_code, usage_reported, prompt_tokens,"
        " completion_tokens, provider_cost, cost_micros FROM analysis_usage WHERE analysis_id = :id"
        " ORDER BY attempt_number",
        {"id": analysis_id},
    )


def test_a_successful_call_stores_its_own_usage(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usage-a", subject="usage-owner-a")
    provider.replies = [reply(VALID, '{"prompt_tokens":812,"completion_tokens":430,"cost":0.00012345}')]

    response = post(api_client, org, "usage-owner-a", "key-usage-a-0001")

    assert response.status_code == 201
    stored = usage_rows(raw_engine, uuid.UUID(response.json()["id"]))
    assert stored == [{
        "attempt_number": 1, "step": "initial", "model_role": "primary", "status": "succeeded", "error_code": None,
        "usage_reported": True, "prompt_tokens": 812, "completion_tokens": 430,
        "provider_cost": Decimal("0.00012345"), "cost_micros": None,
    }]
    attempts = response.json()["attempts"]
    assert attempts[0]["provider_cost"] == "0.00012345"  # exact text in the API, never a float


def test_a_failed_primary_and_a_successful_fallback_keep_separate_usage(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usage-b", subject="usage-owner-b")
    provider.replies = [httpx.Response(503), reply(VALID, '{"prompt_tokens":100,"completion_tokens":200,"cost":0.0005}')]

    response = post(api_client, org, "usage-owner-b", "key-usage-b-0001")

    stored = usage_rows(raw_engine, uuid.UUID(response.json()["id"]))
    assert [(r["model_role"], r["status"], r["usage_reported"], r["provider_cost"]) for r in stored] == [
        ("primary", "failed", False, None),
        ("fallback", "succeeded", True, Decimal("0.0005")),
    ]
    assert response.json()["used_fallback"] is True


def test_a_format_retry_is_a_second_call_with_its_own_cost(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usage-c", subject="usage-owner-c")
    provider.replies = [
        reply(INVALID, '{"prompt_tokens":700,"completion_tokens":90,"cost":0.001}'),
        reply(VALID, '{"prompt_tokens":720,"completion_tokens":640,"cost":0.002}'),
    ]

    response = post(api_client, org, "usage-owner-c", "key-usage-c-0001")

    stored = usage_rows(raw_engine, uuid.UUID(response.json()["id"]))
    assert [(r["step"], r["provider_cost"]) for r in stored] == [
        ("initial", Decimal("0.001")),
        ("format_retry", Decimal("0.002")),
    ]
    assert response.json()["status"] == "succeeded"


def test_an_explicit_zero_is_stored_and_absent_usage_is_stored_as_null(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usage-d", subject="usage-owner-d")
    provider.replies = [reply(VALID, '{"prompt_tokens":5,"completion_tokens":5,"cost":0}')]
    zero = post(api_client, org, "usage-owner-d", "key-usage-d-0001", BODY)
    assert usage_rows(raw_engine, uuid.UUID(zero.json()["id"]))[0]["provider_cost"] == Decimal("0")

    provider.replies = [reply(VALID)]
    other = {"data": {**BODY["data"], "investment": 3300}}
    absent = post(api_client, org, "usage-owner-d", "key-usage-d-0002", other)
    stored = usage_rows(raw_engine, uuid.UUID(absent.json()["id"]))[0]
    assert stored["usage_reported"] is False and stored["provider_cost"] is None and stored["prompt_tokens"] is None


def test_a_malformed_cost_keeps_the_tokens_and_does_not_alter_the_answer(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usage-e", subject="usage-owner-e")
    provider.replies = [reply(VALID, '{"prompt_tokens":40,"completion_tokens":60,"cost":"0.01"}')]

    response = post(api_client, org, "usage-owner-e", "key-usage-e-0001")

    assert response.status_code == 201 and response.json()["status"] == "succeeded"
    stored = usage_rows(raw_engine, uuid.UUID(response.json()["id"]))[0]
    assert (stored["prompt_tokens"], stored["completion_tokens"], stored["provider_cost"]) == (40, 60, None)


def test_an_empty_charged_answer_records_its_cost_on_the_failed_call(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usage-f", subject="usage-owner-f")
    provider.replies = [reply("", '{"prompt_tokens":30,"cost":0.003}'), reply(VALID, '{"prompt_tokens":31,"cost":0.004}')]

    response = post(api_client, org, "usage-owner-f", "key-usage-f-0001")

    stored = usage_rows(raw_engine, uuid.UUID(response.json()["id"]))
    assert [(r["status"], r["error_code"], r["provider_cost"]) for r in stored] == [
        ("failed", "AI_EMPTY", Decimal("0.003")),
        ("succeeded", None, Decimal("0.004")),
    ]


def test_a_timeout_has_no_cost_and_the_analysis_is_stored_as_failed(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usage-g", subject="usage-owner-g")

    provider.replies = [httpx.ReadTimeout("simulated"), httpx.ReadTimeout("simulated")]
    response = post(api_client, org, "usage-owner-g", "key-usage-g-0001")

    assert response.json()["status"] == "failed"
    stored = usage_rows(raw_engine, uuid.UUID(response.json()["id"]))
    assert all(r["usage_reported"] is False and r["provider_cost"] is None for r in stored)
    assert {r["error_code"] for r in stored} == {"AI_TIMEOUT"}


def test_a_replay_adds_no_usage_rows(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usage-h", subject="usage-owner-h")
    provider.replies = [reply(VALID, '{"prompt_tokens":9,"cost":0.0007}')]
    first = post(api_client, org, "usage-owner-h", "key-usage-h-0001")
    before = len(usage_rows(raw_engine, uuid.UUID(first.json()["id"])))

    replay = post(api_client, org, "usage-owner-h", "key-usage-h-0001")

    assert replay.status_code == 200 and provider.calls == 1
    assert len(usage_rows(raw_engine, uuid.UUID(replay.json()["id"]))) == before == 1


def test_another_organization_cannot_read_the_usage_of_an_analysis(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usage-i", subject="usage-owner-i")
    other = owner(database, slug="usage-j", subject="usage-owner-j")
    provider.replies = [reply(VALID, '{"cost":0.0002}')]
    created = post(api_client, org, "usage-owner-i", "key-usage-i-0001")

    response = api_client.get(
        f"/api/v1/organizations/{other}/analyses/{created.json()['id']}",
        headers={"Authorization": f"Bearer {make_token('usage-owner-j')}"},
    )

    assert response.status_code == 404


# --- unknown outcomes and database rules -------------------------------------------------------------------------

def test_a_reserved_call_that_expires_stays_unknown_with_no_cost(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="usage-unknown", org_slug="usage-unknown")
    claim = run(ledger.claim_analysis(
        database, organization_id=org, requested_by=user, campaign_id=None, metric_entry_id=None,
        idempotency_key="key-unknown-0001", input_hash=HASH, prompt_version="analysis-v1", model_requested=PRIMARY,
        deterministic_report=REPORT, lease_seconds=1,
    ))

    async def reserve_then_expire():  # type: ignore[no-untyped-def]
        await ledger.reserve_attempt(
            database, organization_id=org, analysis_id=claim.analysis_id, attempt_number=1,
            step="initial", model_role="primary", model=PRIMARY,
        )
        # Expire the lease the way the ledger tests do (a direct UPDATE), so the outcome does not depend on timing.
        async with raw_engine.connect() as connection:
            await connection.execute(text("UPDATE analyses SET lease_expires_at = now() - interval '1 second' WHERE id = :id"),
                                     {"id": claim.analysis_id})
            await connection.commit()
        return await ledger.abandon_expired(database, organization_id=org, analysis_id=claim.analysis_id)

    assert run(reserve_then_expire()) is True
    stored = usage_rows(raw_engine, claim.analysis_id)
    assert (stored[0]["status"], stored[0]["usage_reported"], stored[0]["provider_cost"]) == ("unknown", False, None)


def test_the_database_refuses_a_cost_on_a_call_without_reported_usage(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="usage-check", org_slug="usage-check")
    claim = run(ledger.claim_analysis(
        database, organization_id=org, requested_by=user, campaign_id=None, metric_entry_id=None,
        idempotency_key="key-check-000001", input_hash=HASH, prompt_version="analysis-v1", model_requested=PRIMARY,
        deterministic_report=REPORT, lease_seconds=120,
    ))
    run(ledger.reserve_attempt(
        database, organization_id=org, analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))

    async def write_cost_without_usage():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            await connection.execute(text("UPDATE analysis_usage SET provider_cost = 0.5 WHERE analysis_id = :id"),
                                     {"id": claim.analysis_id})
            await connection.commit()

    with pytest.raises(DBAPIError, match="provider_cost_reported"):
        run(write_cost_without_usage())


def test_the_database_refuses_a_negative_cost(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="usage-neg", org_slug="usage-neg")
    claim = run(ledger.claim_analysis(
        database, organization_id=org, requested_by=user, campaign_id=None, metric_entry_id=None,
        idempotency_key="key-neg-0000001", input_hash=HASH, prompt_version="analysis-v1", model_requested=PRIMARY,
        deterministic_report=REPORT, lease_seconds=120,
    ))
    run(ledger.reserve_attempt(
        database, organization_id=org, analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))

    async def write_negative():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            await connection.execute(text(
                "UPDATE analysis_usage SET usage_reported = true, provider_cost = -0.5 WHERE analysis_id = :id"),
                {"id": claim.analysis_id})
            await connection.commit()

    with pytest.raises(DBAPIError, match="provider_cost_non_negative"):
        run(write_negative())


def test_sequential_completions_never_precede_their_reservation(database, raw_engine):  # type: ignore[no-untyped-def]
    # Regression: the server clock can step backwards between transactions. Completion time is guarded so that
    # completed_at >= created_at holds regardless (see analysis_ledger._completion_time).
    org, user = seed(database, role=None, subject="usage-clock", org_slug="usage-clock")
    for i in range(40):
        claim = run(ledger.claim_analysis(
            database, organization_id=org, requested_by=user, campaign_id=None, metric_entry_id=None,
            idempotency_key=f"key-clock-{i:06d}", input_hash=HASH, prompt_version="analysis-v1", model_requested=PRIMARY,
            deterministic_report=REPORT, lease_seconds=120,
        ))
        for attempt_number, (step, role) in enumerate([("initial", "primary"), ("initial", "fallback")], 1):
            attempt = run(ledger.reserve_attempt(
                database, organization_id=org, analysis_id=claim.analysis_id, attempt_number=attempt_number,
                step=step, model_role=role, model=PRIMARY,
            ))
            run(ledger.complete_attempt(database, organization_id=org, attempt_id=attempt, status="failed",
                                        error_code="AI_HTTP_ERROR", http_status=500))
        run(ledger.finalize_analysis(database, organization_id=org, analysis_id=claim.analysis_id,
                                     status="failed", error_code="AI_HTTP_ERROR"))
    inverted = rows(raw_engine, "SELECT count(*) AS n FROM analysis_usage WHERE completed_at < created_at")[0]["n"]
    assert inverted == 0
    assert rows(raw_engine, "SELECT count(*) AS n FROM analyses WHERE completed_at < created_at")[0]["n"] == 0


def test_a_network_failure_never_invents_usage():
    def unreachable(request):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("simulated", request=request)

    with pytest.raises(RecoverableAIProviderError) as raised:
        run(call_model_with_usage(settings_for_unit(), "p", [], transport=httpx.MockTransport(unreachable)))
    assert str(raised.value) == "AI_NETWORK"
    assert raised.value.usage is None


def test_a_response_that_is_not_json_has_no_usage_and_is_an_empty_answer():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"<html>gateway</html>"))
    with pytest.raises(RecoverableAIProviderError) as raised:
        run(call_model_with_usage(settings_for_unit(), "p", [], transport=transport))
    assert str(raised.value) == "AI_EMPTY"
    assert raised.value.usage is None
