"""Write protocol for analyses, run against PostgreSQL with a simulated provider.

The provider is a list of scripted answers. It records every call and never touches the network, so no test can
bill the model. The driver below sequences the ledger the way the service will; the database refuses anything that
breaks the rules, and the tests check both the accepted path and the refused ones.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import analysis_ledger as ledger
from app.db.analysis_ledger import ProviderUsage
from app.db.repositories import OrganizationRepository, UserRepository
from app.services.analysis import is_parseable, sanitize
from tests.conftest import run

INPUT_HASH = "c" * 64
REPORT = {"investment": 4850000, "ctr": 2.1}
PRIMARY = "model-primary"
FALLBACK = "model-fallback"
LEASE_SECONDS = 120
VALID_ANSWER = "## Estado general\n🟡 Requiere atención\n## Resumen ejecutivo\nResumen de prueba."
INVALID_ANSWER = "Respuesta sin el formato obligatorio."


class SimulatedProvider:
    """Scripted outcomes, one per HTTP call. Records each call. Optionally observes the database during a call."""

    def __init__(self, *script, before_call=None):  # type: ignore[no-untyped-def]
        self.script = list(script)
        self.calls: list[str] = []
        self.before_call = before_call  # async hook: runs inside the simulated call, before it answers

    def call(self, model: str):  # type: ignore[no-untyped-def]
        self.calls.append(model)
        assert self.script, "the simulated provider was called more times than the test scripted"
        return self.script.pop(0)


def answer(text_value: str = VALID_ANSWER, usage: ProviderUsage | None = None) -> dict:
    return {"ok": True, "text": text_value, "usage": usage}


def fail(code: str, http_status: int | None = None) -> dict:
    return {"ok": False, "code": code, "http_status": http_status}


async def drive(database, provider: SimulatedProvider, *, org, user, key, input_hash=INPUT_HASH, campaign=None, entry=None):  # type: ignore[no-untyped-def]
    """Sequences one request the way the service will: claim, reserve, call, complete, finalize.

    The first answer decides the outcome. A format failure triggers one format retry, as in app.services.analysis.
    """
    claim = await ledger.claim_analysis(
        database,
        organization_id=org,
        requested_by=user,
        campaign_id=campaign,
        metric_entry_id=entry,
        idempotency_key=key,
        input_hash=input_hash,
        prompt_version="analysis-v1",
        model_requested=PRIMARY,
        deterministic_report=REPORT,
        lease_seconds=LEASE_SECONDS,
    )
    if claim.outcome != "claimed":
        return claim

    texts: list[tuple[str, str, bool]] = []  # (text, model, used_fallback) of each answer that arrived
    last_error = None
    number = 0
    for step in ("initial", "format_retry"):
        if step == "format_retry" and (not texts or is_parseable(sanitize(texts[0][0]))):
            break
        step_answered = False
        for role, model in (("primary", PRIMARY), ("fallback", FALLBACK)):
            if role == "fallback" and last_error is None:
                break
            number += 1
            attempt_id = await ledger.reserve_attempt(
                database, organization_id=org, analysis_id=claim.analysis_id, attempt_number=number,
                step=step, model_role=role, model=model,
            )
            if provider.before_call is not None:
                await provider.before_call()
            outcome = provider.call(model)
            if outcome["ok"]:
                await ledger.complete_attempt(
                    database, organization_id=org, attempt_id=attempt_id, status="succeeded", usage=outcome["usage"],
                )
                texts.append((outcome["text"], model, role == "fallback"))
                step_answered = True
                last_error = None
                break
            await ledger.complete_attempt(
                database, organization_id=org, attempt_id=attempt_id, status="failed",
                error_code=outcome["code"], http_status=outcome["http_status"],
            )
            last_error = outcome["code"]
        if not step_answered:
            break

    if not texts:
        await ledger.finalize_analysis(
            database, organization_id=org, analysis_id=claim.analysis_id, status="failed",
            error_code=last_error or "INTERNAL_ERROR",
        )
    else:
        final_text, model_used, used_fallback = texts[-1]
        if not is_parseable(sanitize(final_text)) and len(texts) > 1:
            final_text, model_used, used_fallback = texts[0]
        if not is_parseable(sanitize(final_text)):
            # Same rule as the service: a format failure after the allowed retry is a failed analysis.
            await ledger.finalize_analysis(
                database, organization_id=org, analysis_id=claim.analysis_id, status="failed",
                error_code="AI_INVALID_RESPONSE",
            )
            return claim
        await ledger.finalize_analysis(
            database, organization_id=org, analysis_id=claim.analysis_id, status="succeeded",
            analysis_markdown=sanitize(final_text), model_used=model_used, used_fallback=used_fallback,
            format_valid=True, latency_ms=1,
        )
    return claim


@pytest.fixture
def tenant(database):  # type: ignore[no-untyped-def]
    async def create():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            org = await OrganizationRepository(session).create(name="Cliente A", slug="cliente-a")
            other = await OrganizationRepository(session).create(name="Cliente B", slug="cliente-b")
            user = await UserRepository(session).create(external_subject="ledger-user")
            return org.id, other.id, user.id

    org, other, user = run(create())
    return {"org": org, "other": other, "user": user}


def rows(raw_engine, sql: str, params: dict | None = None):  # type: ignore[no-untyped-def]
    async def read():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            return [dict(row._mapping) for row in (await connection.execute(text(sql), params or {})).all()]

    return run(read())


def attempts(raw_engine, analysis_id):  # type: ignore[no-untyped-def]
    return rows(
        raw_engine,
        "SELECT attempt_number, step, model_role, model, status, error_code, http_status, usage_reported,"
        " prompt_tokens, completion_tokens, cost_micros FROM analysis_usage WHERE analysis_id = :id"
        " ORDER BY attempt_number",
        {"id": analysis_id},
    )


def analysis(raw_engine, analysis_id):  # type: ignore[no-untyped-def]
    return rows(
        raw_engine,
        "SELECT status, error_code, model_used, used_fallback, format_valid, analysis_markdown, lease_expires_at"
        " FROM analyses WHERE id = :id",
        {"id": analysis_id},
    )[0]


def run_driver(database, provider, tenant, key, **overrides):  # type: ignore[no-untyped-def]
    values = {"org": tenant["org"], "user": tenant["user"], "key": key}
    values.update(overrides)
    return run(drive(database, provider, **values))


def expire_lease(raw_engine, analysis_id):  # type: ignore[no-untyped-def]
    run(_execute(raw_engine, "UPDATE analyses SET lease_expires_at = now() - interval '1 second' WHERE id = :id",
                 {"id": analysis_id}))


async def _execute(raw_engine, sql: str, params: dict) -> None:  # type: ignore[no-untyped-def]
    async with raw_engine.begin() as connection:
        await connection.execute(text(sql), params)


# --- accepted paths ------------------------------------------------------------------------------------------------

def test_a_successful_first_call_is_recorded_as_one_ordered_attempt(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    provider = SimulatedProvider(answer())
    claim = run_driver(database, provider, tenant, "k-ok")

    assert provider.calls == [PRIMARY]
    stored = analysis(raw_engine, claim.analysis_id)
    assert stored["status"] == "succeeded" and stored["format_valid"] is True and stored["used_fallback"] is False
    [first] = attempts(raw_engine, claim.analysis_id)
    assert (first["attempt_number"], first["step"], first["model_role"], first["status"]) == (1, "initial", "primary", "succeeded")
    assert first["usage_reported"] is False
    assert first["prompt_tokens"] is None and first["completion_tokens"] is None and first["cost_micros"] is None


def test_provider_usage_is_stored_exactly_as_reported(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    provider = SimulatedProvider(answer(usage=ProviderUsage(prompt_tokens=812, completion_tokens=430, cost_micros=1875)))
    claim = run_driver(database, provider, tenant, "k-usage")

    [first] = attempts(raw_engine, claim.analysis_id)
    assert first["usage_reported"] is True
    assert (first["prompt_tokens"], first["completion_tokens"], first["cost_micros"]) == (812, 430, 1875)


def test_a_timeout_on_the_primary_model_falls_back_in_the_same_step(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    provider = SimulatedProvider(fail("AI_TIMEOUT"), answer())
    claim = run_driver(database, provider, tenant, "k-fallback")

    assert provider.calls == [PRIMARY, FALLBACK]
    attempts_stored = attempts(raw_engine, claim.analysis_id)
    assert [(a["model_role"], a["status"], a["error_code"]) for a in attempts_stored] == [
        ("primary", "failed", "AI_TIMEOUT"),
        ("fallback", "succeeded", None),
    ]
    stored = analysis(raw_engine, claim.analysis_id)
    assert stored["status"] == "succeeded" and stored["used_fallback"] is True and stored["model_used"] == FALLBACK


def test_a_format_failure_triggers_one_format_retry(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    provider = SimulatedProvider(answer(INVALID_ANSWER), answer())
    claim = run_driver(database, provider, tenant, "k-format")

    assert provider.calls == [PRIMARY, PRIMARY]
    assert [(a["step"], a["model_role"]) for a in attempts(raw_engine, claim.analysis_id)] == [
        ("initial", "primary"),
        ("format_retry", "primary"),
    ]
    assert analysis(raw_engine, claim.analysis_id)["format_valid"] is True


def test_a_failed_format_retry_falls_back_and_uses_the_fallback_answer(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    provider = SimulatedProvider(answer(INVALID_ANSWER), fail("AI_HTTP_ERROR", 502), answer())
    claim = run_driver(database, provider, tenant, "k-retry-fallback")

    assert provider.calls == [PRIMARY, PRIMARY, FALLBACK]
    stored = analysis(raw_engine, claim.analysis_id)
    assert stored["status"] == "succeeded" and stored["used_fallback"] is True and stored["model_used"] == FALLBACK


def test_an_answer_that_stays_invalid_is_stored_as_a_failure_never_as_succeeded(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    provider = SimulatedProvider(answer(INVALID_ANSWER), answer(INVALID_ANSWER))
    claim = run_driver(database, provider, tenant, "k-still-invalid")

    stored = analysis(raw_engine, claim.analysis_id)
    assert (stored["status"], stored["error_code"]) == ("failed", "AI_INVALID_RESPONSE")
    assert stored["analysis_markdown"] is None and stored["format_valid"] is None
    assert len(attempts(raw_engine, claim.analysis_id)) == 2


def test_when_every_call_fails_the_analysis_fails_with_the_last_safe_code(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    provider = SimulatedProvider(fail("AI_NETWORK"), fail("AI_HTTP_ERROR", 503))
    claim = run_driver(database, provider, tenant, "k-all-fail")

    assert provider.calls == [PRIMARY, FALLBACK]
    stored = analysis(raw_engine, claim.analysis_id)
    assert stored["status"] == "failed" and stored["error_code"] == "AI_HTTP_ERROR"
    assert stored["analysis_markdown"] is None and stored["model_used"] is None


# --- idempotency and concurrency ------------------------------------------------------------------------------------

def test_concurrent_requests_with_one_key_start_exactly_one_analysis(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    first, second = SimulatedProvider(answer()), SimulatedProvider(answer())

    async def race():  # type: ignore[no-untyped-def]
        return await asyncio.gather(
            drive(database, first, org=tenant["org"], user=tenant["user"], key="k-race"),
            drive(database, second, org=tenant["org"], user=tenant["user"], key="k-race"),
        )

    outcomes = sorted(claim.outcome for claim in run(race()))
    assert outcomes == ["claimed", "existing"]
    assert len(first.calls) + len(second.calls) == 1
    assert len(rows(raw_engine, "SELECT id FROM analyses WHERE idempotency_key = 'k-race'")) == 1


def test_the_same_key_with_different_input_is_refused_without_a_call(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    provider = SimulatedProvider(answer())
    run_driver(database, provider, tenant, "k-reuse")

    other = SimulatedProvider()
    claim = run_driver(database, other, tenant, "k-reuse", input_hash="d" * 64)
    assert claim.outcome == "reused"
    assert other.calls == []


def test_a_completed_key_returns_the_stored_result_without_a_call(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    run_driver(database, SimulatedProvider(answer()), tenant, "k-done")

    again = SimulatedProvider()
    claim = run_driver(database, again, tenant, "k-done")
    assert claim.outcome == "existing" and claim.status == "succeeded"
    assert claim.analysis_markdown and claim.model_used == PRIMARY
    assert again.calls == []


# --- leases, abandonment and zombie owners ------------------------------------------------------------------------

def test_an_expired_lease_is_abandoned_and_never_called_again(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    crashed = SimulatedProvider(fail("AI_TIMEOUT"))
    claim = ledger_claim(database, tenant, "k-crash")
    attempt_id = run(ledger.reserve_attempt(
        database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))
    crashed.call(PRIMARY)  # the call happens; the process dies before recording its outcome
    expire_lease(raw_engine, claim.analysis_id)

    again = run_driver(database, SimulatedProvider(), tenant, "k-crash")
    assert again.outcome == "expired"
    assert run(ledger.abandon_expired(database, organization_id=tenant["org"], analysis_id=claim.analysis_id)) is True

    [attempt_row] = attempts(raw_engine, claim.analysis_id)
    assert (attempt_row["status"], attempt_row["error_code"]) == ("unknown", "ANALYSIS_ABANDONED")
    stored = analysis(raw_engine, claim.analysis_id)
    assert (stored["status"], stored["error_code"]) == ("failed", "ANALYSIS_ABANDONED")

    retry = SimulatedProvider()
    later = run_driver(database, retry, tenant, "k-crash")
    assert later.outcome == "existing" and later.status == "failed" and retry.calls == []
    assert len(crashed.calls) == 1  # the only paid call was the one before the crash


def ledger_claim(database, tenant, key):  # type: ignore[no-untyped-def]
    return run(ledger.claim_analysis(
        database, organization_id=tenant["org"], requested_by=tenant["user"], campaign_id=None,
        metric_entry_id=None, idempotency_key=key, input_hash=INPUT_HASH, prompt_version="analysis-v1",
        model_requested=PRIMARY, deterministic_report=REPORT, lease_seconds=LEASE_SECONDS,
    ))


def test_abandonment_is_refused_while_the_lease_is_live(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    claim = ledger_claim(database, tenant, "k-live")
    run(ledger.reserve_attempt(
        database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))
    with pytest.raises(DBAPIError, match="only after the lease expires"):
        run(ledger.abandon_expired(database, organization_id=tenant["org"], analysis_id=claim.analysis_id))
    assert analysis(raw_engine, claim.analysis_id)["status"] == "running"


def test_an_expired_lease_refuses_new_calls_completions_and_the_final_write(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    claim = ledger_claim(database, tenant, "k-expired-owner")
    attempt_id = run(ledger.reserve_attempt(
        database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))
    expire_lease(raw_engine, claim.analysis_id)

    with pytest.raises(DBAPIError, match="cannot be completed after (its|the) lease expires"):
        run(ledger.complete_attempt(
            database, organization_id=tenant["org"], attempt_id=attempt_id, status="succeeded",
        ))
    with pytest.raises(DBAPIError, match="cannot be completed after its lease expires"):
        run(ledger.finalize_analysis(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, status="succeeded",
            analysis_markdown=VALID_ANSWER, model_used=PRIMARY, format_valid=True,
        ))
    with pytest.raises(DBAPIError, match="needs a running analysis with a live lease|still reserved"):
        run(ledger.reserve_attempt(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=2,
            step="initial", model_role="fallback", model=FALLBACK,
        ))


def test_a_zombie_owner_cannot_overwrite_an_abandoned_analysis(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    claim = ledger_claim(database, tenant, "k-zombie")
    attempt_id = run(ledger.reserve_attempt(
        database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))
    expire_lease(raw_engine, claim.analysis_id)
    assert run(ledger.abandon_expired(database, organization_id=tenant["org"], analysis_id=claim.analysis_id))

    with pytest.raises(DBAPIError, match="immutable"):
        run(ledger.complete_attempt(database, organization_id=tenant["org"], attempt_id=attempt_id, status="succeeded"))
    with pytest.raises(DBAPIError, match="immutable"):
        run(ledger.finalize_analysis(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, status="succeeded",
            analysis_markdown=VALID_ANSWER, model_used=PRIMARY, format_valid=True,
        ))
    assert analysis(raw_engine, claim.analysis_id)["status"] == "failed"


# --- the database refuses broken sequences -------------------------------------------------------------------------

def test_the_database_refuses_out_of_order_attempts(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    claim = ledger_claim(database, tenant, "k-order")

    with pytest.raises(DBAPIError, match="first attempt must be the primary model"):
        run(ledger.reserve_attempt(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=1,
            step="initial", model_role="fallback", model=FALLBACK,
        ))
    with pytest.raises(DBAPIError, match="consecutively"):
        run(ledger.reserve_attempt(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=2,
            step="initial", model_role="primary", model=PRIMARY,
        ))
    first = run(ledger.reserve_attempt(
        database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))
    run(ledger.complete_attempt(database, organization_id=tenant["org"], attempt_id=first, status="failed",
                                error_code="AI_TIMEOUT"))
    with pytest.raises(DBAPIError, match="a format retry needs an answer"):
        run(ledger.reserve_attempt(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=2,
            step="format_retry", model_role="primary", model=PRIMARY,
        ))


def test_the_database_refuses_a_second_call_in_flight_and_a_fifth_attempt(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    claim = ledger_claim(database, tenant, "k-inflight")
    run(ledger.reserve_attempt(
        database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))
    with pytest.raises(DBAPIError, match="still reserved"):
        run(ledger.reserve_attempt(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=2,
            step="initial", model_role="fallback", model=FALLBACK,
        ))


def test_the_database_refuses_more_than_four_attempts(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    """The numbering trigger runs before the CHECK constraint, so either message proves the refusal."""
    claim = ledger_claim(database, tenant, "k-five")
    with pytest.raises(DBAPIError, match="consecutively|ck_analysis_usage_attempt_number_range"):
        run(ledger.reserve_attempt(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=5,
            step="initial", model_role="primary", model=PRIMARY,
        ))


def test_the_database_refuses_finalizing_while_a_call_is_reserved(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    claim = ledger_claim(database, tenant, "k-reserved-final")
    run(ledger.reserve_attempt(
        database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))
    with pytest.raises(DBAPIError, match="while an attempt is reserved"):
        run(ledger.finalize_analysis(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, status="failed",
            error_code="AI_TIMEOUT",
        ))


def test_the_final_answer_must_match_a_succeeded_attempt(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    claim = ledger_claim(database, tenant, "k-match")
    attempt_id = run(ledger.reserve_attempt(
        database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))
    run(ledger.complete_attempt(database, organization_id=tenant["org"], attempt_id=attempt_id, status="succeeded"))

    with pytest.raises(DBAPIError, match="must match a succeeded attempt"):
        run(ledger.finalize_analysis(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, status="succeeded",
            analysis_markdown=VALID_ANSWER, model_used="another-model", format_valid=True,
        ))
    with pytest.raises(DBAPIError, match="must match a succeeded attempt"):
        run(ledger.finalize_analysis(
            database, organization_id=tenant["org"], analysis_id=claim.analysis_id, status="succeeded",
            analysis_markdown=VALID_ANSWER, model_used=PRIMARY, used_fallback=True, format_valid=True,
        ))


# --- deterministic report and isolation ---------------------------------------------------------------------------

def test_the_deterministic_report_is_stored_with_the_claim_and_never_changes(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    claim = ledger_claim(database, tenant, "k-report")
    [stored] = rows(raw_engine, "SELECT deterministic_report FROM analyses WHERE id = :id", {"id": claim.analysis_id})
    assert stored["deterministic_report"] == REPORT

    with pytest.raises(DBAPIError, match="inputs of an analysis are immutable"):
        run(_execute(raw_engine, "UPDATE analyses SET deterministic_report = '{\"forged\": true}' WHERE id = :id",
                     {"id": claim.analysis_id}))


def test_a_claim_cannot_reference_another_organizations_campaign(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    campaign = uuid.uuid4()
    run(_execute(raw_engine, "INSERT INTO campaigns (id, organization_id, name) VALUES (:id, :org, 'Campaña')",
                 {"id": campaign, "org": tenant["org"]}))
    with pytest.raises(DBAPIError, match="fk_analyses_campaign"):
        run(ledger.claim_analysis(
            database, organization_id=tenant["other"], requested_by=tenant["user"], campaign_id=campaign,
            metric_entry_id=None, idempotency_key="k-cross", input_hash=INPUT_HASH, prompt_version="analysis-v1",
            model_requested=PRIMARY, deterministic_report=REPORT, lease_seconds=LEASE_SECONDS,
        ))


def test_another_organization_cannot_complete_or_finalize_an_attempt(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    claim = ledger_claim(database, tenant, "k-foreign")
    attempt_id = run(ledger.reserve_attempt(
        database, organization_id=tenant["org"], analysis_id=claim.analysis_id, attempt_number=1,
        step="initial", model_role="primary", model=PRIMARY,
    ))
    with pytest.raises(LookupError):
        run(ledger.complete_attempt(database, organization_id=tenant["other"], attempt_id=attempt_id,
                                    status="succeeded"))
    with pytest.raises(LookupError):
        run(ledger.finalize_analysis(database, organization_id=tenant["other"], analysis_id=claim.analysis_id,
                                     status="failed", error_code="AI_TIMEOUT"))


# --- the commit happens before the provider is called -----------------------------------------------------------------

def test_the_claim_and_the_reservation_are_visible_before_the_provider_is_called(database, tenant, raw_engine):  # type: ignore[no-untyped-def]
    seen: dict = {}

    async def observe() -> None:
        # Runs inside the simulated call. A separate connection must already see the committed rows.
        async with raw_engine.connect() as connection:
            seen["analyses"] = [dict(r._mapping) for r in (await connection.execute(text(
                "SELECT status FROM analyses WHERE idempotency_key = 'k-visible'"))).all()]
            seen["reserved"] = [dict(r._mapping) for r in (await connection.execute(text(
                "SELECT status FROM analysis_usage WHERE status = 'reserved'"))).all()]

    run_driver(database, SimulatedProvider(answer(), before_call=observe), tenant, "k-visible")
    assert seen["analyses"] == [{"status": "running"}]
    assert seen["reserved"] == [{"status": "reserved"}]
