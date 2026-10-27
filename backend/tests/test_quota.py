"""Analysis quotas: monthly units per organization, atomic consumption, release rules, and the HTTP contract.

Runs against the test database. The provider is the simulated transport from test_analysis_api; no test reaches
OpenRouter or any paid endpoint. Policy: docs/PHASE2_QUOTAS.md.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import cli
from app.core.config import QuotaConfigurationError, analysis_quota_enforced
from app.db import analysis_ledger as ledger
from app.domain.quota import QuotaExceededError, QuotaNotConfiguredError, quota_period_for
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import make_token
from tests.conftest import run
from tests.test_analysis_api import BODY, INVALID, VALID, ok, owner, post, provider  # noqa: F401  (pytest fixtures)
from tests.test_analysis_ledger import PRIMARY, REPORT, rows
from tests.test_campaign_api import seed

PERIOD = date(2026, 10, 1)
NEXT_PERIOD = date(2026, 11, 1)
HASH = "d" * 64
LEASE = 120


# --- helpers ------------------------------------------------------------------------------------------------------

def set_quota(database, organization_id, limit: int, plan: str = "pilot") -> None:  # type: ignore[no-untyped-def]
    async def write():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            await session.execute(
                text("INSERT INTO analysis_quotas (organization_id, plan, monthly_analysis_limit)"
                     " VALUES (:org, :plan, :limit)"),
                {"org": organization_id, "plan": plan, "limit": limit},
            )

    run(write())


def unit_counts(raw_engine, organization_id, period: date = PERIOD) -> tuple[int, int] | None:  # type: ignore[no-untyped-def]
    found = rows(
        raw_engine,
        "SELECT consumed, released FROM analysis_quota_usage WHERE organization_id = :org AND period_start = :period",
        {"org": organization_id, "period": period},
    )
    return (found[0]["consumed"], found[0]["released"]) if found else None


def analysis_count(raw_engine, organization_id) -> int:  # type: ignore[no-untyped-def]
    return rows(raw_engine, "SELECT count(*) AS n FROM analyses WHERE organization_id = :org", {"org": organization_id})[0]["n"]


async def _claim(database, *, org, user, key, period=PERIOD, enforce=True, lease=LEASE):  # type: ignore[no-untyped-def]
    return await ledger.claim_analysis(
        database,
        organization_id=org,
        requested_by=user,
        campaign_id=None,
        metric_entry_id=None,
        idempotency_key=key,
        input_hash=HASH,
        prompt_version="analysis-v1",
        model_requested=PRIMARY,
        deterministic_report=REPORT,
        lease_seconds=lease,
        quota_period=period,
        enforce_quota=enforce,
    )


def claim(database, *, org, user, key, period=PERIOD, enforce=True, lease=LEASE):  # type: ignore[no-untyped-def]
    return run(_claim(database, org=org, user=user, key=key, period=period, enforce=enforce, lease=lease))


# --- policy: period and configuration -----------------------------------------------------------------------------

def test_the_period_is_the_first_day_of_the_utc_month():
    late_in_colombia = datetime(2026, 10, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5)))  # 2026-11-01 04:30 UTC
    assert quota_period_for(late_in_colombia) == NEXT_PERIOD
    assert quota_period_for(datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)) == PERIOD


def test_a_naive_datetime_is_refused_because_its_month_would_depend_on_the_server_zone():
    with pytest.raises(ValueError):
        quota_period_for(datetime(2026, 10, 1))


@pytest.mark.parametrize(
    "raw, expected",
    [(None, False), ("", False), ("false", False), ("0", False), ("true", True), ("ON", True), ("yes", True)],
)
def test_the_enforcement_flag_is_read_strictly(monkeypatch, raw, expected):  # type: ignore[no-untyped-def]
    if raw is None:
        monkeypatch.delenv("ANALYSIS_QUOTA_ENFORCED", raising=False)
    else:
        monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", raw)
    assert analysis_quota_enforced() is expected


def test_an_unknown_enforcement_value_fails_closed(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "sometimes")
    with pytest.raises(QuotaConfigurationError):
        analysis_quota_enforced()


# --- claim: consumption and refusal -------------------------------------------------------------------------------

def test_a_claim_with_available_quota_consumes_one_unit_and_records_its_month(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-a", org_slug="quota-a")
    set_quota(database, org, limit=2)

    result = claim(database, org=org, user=user, key="key-quota-0001")

    assert result.outcome == "claimed"
    assert unit_counts(raw_engine, org) == (1, 0)
    stored = rows(raw_engine, "SELECT quota_period_start FROM analyses WHERE id = :id", {"id": result.analysis_id})
    assert stored[0]["quota_period_start"] == PERIOD


def test_an_exhausted_quota_refuses_the_claim_and_stores_nothing(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-b", org_slug="quota-b")
    set_quota(database, org, limit=1)
    claim(database, org=org, user=user, key="key-quota-0001")

    with pytest.raises(QuotaExceededError):
        claim(database, org=org, user=user, key="key-quota-0002")

    assert analysis_count(raw_engine, org) == 1
    assert unit_counts(raw_engine, org) == (1, 0)


def test_two_concurrent_claims_with_one_unit_left_admit_exactly_one(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-c", org_slug="quota-c")
    set_quota(database, org, limit=1)

    async def race():  # type: ignore[no-untyped-def]
        return await asyncio.gather(
            _claim(database, org=org, user=user, key="key-race-quota-01"),
            _claim(database, org=org, user=user, key="key-race-quota-02"),
            return_exceptions=True,
        )

    results = run(race())
    claimed = [r for r in results if getattr(r, "outcome", None) == "claimed"]
    refused = [r for r in results if isinstance(r, QuotaExceededError)]
    assert len(claimed) == 1 and len(refused) == 1, results
    assert analysis_count(raw_engine, org) == 1
    assert unit_counts(raw_engine, org) == (1, 0)


def test_a_replay_of_the_same_key_consumes_nothing_even_when_exhausted(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-d", org_slug="quota-d")
    set_quota(database, org, limit=1)
    claim(database, org=org, user=user, key="key-replay-quota-1")

    replay = claim(database, org=org, user=user, key="key-replay-quota-1")

    assert replay.outcome == "existing"
    assert unit_counts(raw_engine, org) == (1, 0)


def test_quota_is_isolated_per_organization(database, raw_engine):  # type: ignore[no-untyped-def]
    org_a, user_a = seed(database, role=None, subject="quota-iso-a", org_slug="quota-iso-a")
    org_b, user_b = seed(database, role=None, subject="quota-iso-b", org_slug="quota-iso-b")
    set_quota(database, org_a, limit=1)
    set_quota(database, org_b, limit=1)
    claim(database, org=org_a, user=user_a, key="key-iso-a-00001")

    with pytest.raises(QuotaExceededError):
        claim(database, org=org_a, user=user_a, key="key-iso-a-00002")
    other = claim(database, org=org_b, user=user_b, key="key-iso-b-00001")

    assert other.outcome == "claimed"
    assert unit_counts(raw_engine, org_b) == (1, 0)
    assert unit_counts(raw_engine, org_a) == (1, 0)


def test_each_utc_month_has_its_own_units(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-month", org_slug="quota-month")
    set_quota(database, org, limit=1)
    claim(database, org=org, user=user, key="key-month-00001", period=PERIOD)

    with pytest.raises(QuotaExceededError):
        claim(database, org=org, user=user, key="key-month-00002", period=PERIOD)
    november = claim(database, org=org, user=user, key="key-month-00003", period=NEXT_PERIOD)

    assert november.outcome == "claimed"
    assert unit_counts(raw_engine, org, PERIOD) == (1, 0)
    assert unit_counts(raw_engine, org, NEXT_PERIOD) == (1, 0)


def test_enforced_without_a_quota_row_refuses_and_stores_nothing(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-none", org_slug="quota-none")

    with pytest.raises(QuotaNotConfiguredError):
        claim(database, org=org, user=user, key="key-none-00001")

    assert analysis_count(raw_engine, org) == 0
    assert unit_counts(raw_engine, org) is None


def test_without_enforcement_the_unit_is_counted_and_never_refused(database, raw_engine):  # type: ignore[no-untyped-def]
    org_none, user_none = seed(database, role=None, subject="quota-off-a", org_slug="quota-off-a")
    org_zero, user_zero = seed(database, role=None, subject="quota-off-b", org_slug="quota-off-b")
    set_quota(database, org_zero, limit=0)

    claim(database, org=org_none, user=user_none, key="key-off-a-00001", enforce=False)
    claim(database, org=org_none, user=user_none, key="key-off-a-00002", enforce=False)
    zero = claim(database, org=org_zero, user=user_zero, key="key-off-b-00001", enforce=False)

    assert zero.outcome == "claimed"
    assert unit_counts(raw_engine, org_none) == (2, 0)
    assert unit_counts(raw_engine, org_zero) == (1, 0)


def test_a_database_error_during_the_reservation_rolls_back_the_claim(database, raw_engine, monkeypatch):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-fault", org_slug="quota-fault")
    set_quota(database, org, limit=1)

    async def broken(session, **kwargs):  # type: ignore[no-untyped-def]
        raise DBAPIError("UPDATE analysis_quota_usage", None, Exception("injected failure"))

    monkeypatch.setattr(ledger, "_consume_unit", broken)
    with pytest.raises(DBAPIError):
        claim(database, org=org, user=user, key="key-fault-00001")

    assert analysis_count(raw_engine, org) == 0
    assert unit_counts(raw_engine, org) is None


# --- release: only when no provider call was reserved -------------------------------------------------------------

def test_a_failure_without_any_provider_call_gives_the_unit_back(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-release", org_slug="quota-release")
    set_quota(database, org, limit=1)
    first = claim(database, org=org, user=user, key="key-release-0001")

    run(ledger.finalize_analysis(
        database, organization_id=org, analysis_id=first.analysis_id, status="failed", error_code="AI_NOT_CONFIGURED",
    ))

    assert unit_counts(raw_engine, org) == (0, 1)
    assert claim(database, org=org, user=user, key="key-release-0002").outcome == "claimed"
    assert unit_counts(raw_engine, org) == (1, 1)


def test_a_failure_after_a_provider_call_keeps_the_unit(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-keep", org_slug="quota-keep")
    set_quota(database, org, limit=1)
    first = claim(database, org=org, user=user, key="key-keep-000001")

    async def call_then_fail():  # type: ignore[no-untyped-def]
        attempt = await ledger.reserve_attempt(
            database, organization_id=org, analysis_id=first.analysis_id, attempt_number=1,
            step="initial", model_role="primary", model=PRIMARY,
        )
        await ledger.complete_attempt(
            database, organization_id=org, attempt_id=attempt, status="failed", error_code="AI_HTTP_ERROR", http_status=401,
        )
        await ledger.finalize_analysis(
            database, organization_id=org, analysis_id=first.analysis_id, status="failed", error_code="AI_HTTP_ERROR",
        )

    run(call_then_fail())

    assert unit_counts(raw_engine, org) == (1, 0)


def test_an_abandoned_analysis_without_calls_gives_the_unit_back(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-abandon", org_slug="quota-abandon")
    set_quota(database, org, limit=1)
    first = claim(database, org=org, user=user, key="key-abandon-0001", lease=1)

    async def let_the_lease_expire():  # type: ignore[no-untyped-def]
        await asyncio.sleep(1.2)
        return await ledger.abandon_expired(database, organization_id=org, analysis_id=first.analysis_id)

    assert run(let_the_lease_expire()) is True
    assert unit_counts(raw_engine, org) == (0, 1)


def test_an_abandoned_analysis_with_a_reserved_call_keeps_the_unit(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-stuck", org_slug="quota-stuck")
    set_quota(database, org, limit=1)
    first = claim(database, org=org, user=user, key="key-stuck-00001", lease=1)

    async def reserve_then_expire():  # type: ignore[no-untyped-def]
        await ledger.reserve_attempt(
            database, organization_id=org, analysis_id=first.analysis_id, attempt_number=1,
            step="initial", model_role="primary", model=PRIMARY,
        )
        await asyncio.sleep(1.2)
        return await ledger.abandon_expired(database, organization_id=org, analysis_id=first.analysis_id)

    assert run(reserve_then_expire()) is True
    assert unit_counts(raw_engine, org) == (1, 0)


# --- HTTP contract ------------------------------------------------------------------------------------------------

OTHER_BODY = {"data": {**BODY["data"], "investment": 2500}}


def _enforce(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "true")


def test_an_exhausted_quota_answers_429_without_a_provider_call(api_client, database, provider, monkeypatch, raw_engine):  # type: ignore[no-untyped-def]
    _enforce(monkeypatch)
    org = owner(database, slug="quota-api-a", subject="quota-owner-a")
    set_quota(database, org, limit=1)
    provider.replies = [ok(VALID)]
    assert post(api_client, org, "quota-owner-a", "key-api-quota-001").status_code == 201

    response = post(api_client, org, "quota-owner-a", "key-api-quota-002", OTHER_BODY)

    assert response.status_code == 429
    assert response.json()["detail"] == "QUOTA_EXCEEDED"
    assert provider.calls == 1
    assert analysis_count(raw_engine, org) == 1


def test_a_replay_is_answered_even_when_the_quota_is_exhausted(api_client, database, provider, monkeypatch):  # type: ignore[no-untyped-def]
    _enforce(monkeypatch)
    org = owner(database, slug="quota-api-b", subject="quota-owner-b")
    set_quota(database, org, limit=1)
    provider.replies = [ok(VALID)]

    first = post(api_client, org, "quota-owner-b", "key-api-replay-1")
    second = post(api_client, org, "quota-owner-b", "key-api-replay-1")

    assert first.status_code == 201
    assert second.status_code == 200 and second.headers["Idempotent-Replayed"] == "true"
    assert provider.calls == 1


def test_enforced_without_a_quota_row_answers_503_without_a_provider_call(api_client, database, provider, monkeypatch, raw_engine):  # type: ignore[no-untyped-def]
    _enforce(monkeypatch)
    org = owner(database, slug="quota-api-c", subject="quota-owner-c")

    response = post(api_client, org, "quota-owner-c", "key-api-noquota-1")

    assert response.status_code == 503
    assert response.json()["detail"] == "QUOTA_NOT_CONFIGURED"
    assert provider.calls == 0
    assert analysis_count(raw_engine, org) == 0


def test_an_invalid_enforcement_value_answers_503_and_claims_nothing(api_client, database, provider, monkeypatch, raw_engine):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "sometimes")
    org = owner(database, slug="quota-api-d", subject="quota-owner-d")

    response = post(api_client, org, "quota-owner-d", "key-api-badflag-1")

    assert response.status_code == 503
    assert response.json()["detail"] == "QUOTA_CONFIGURATION_INVALID"
    assert provider.calls == 0
    assert analysis_count(raw_engine, org) == 0


def test_a_failed_analysis_that_called_the_provider_keeps_its_unit(api_client, database, provider, monkeypatch, raw_engine):  # type: ignore[no-untyped-def]
    _enforce(monkeypatch)
    org = owner(database, slug="quota-api-e", subject="quota-owner-e")
    set_quota(database, org, limit=1)
    provider.replies = [httpx.Response(401), httpx.Response(401)]

    failed = post(api_client, org, "quota-owner-e", "key-api-failed-01")

    assert failed.json()["status"] == "failed"
    assert unit_counts(raw_engine, org, _current_period()) == (1, 0)
    assert post(api_client, org, "quota-owner-e", "key-api-failed-02", OTHER_BODY).status_code == 429


def test_a_format_retry_never_adds_a_unit(api_client, database, provider, monkeypatch, raw_engine):  # type: ignore[no-untyped-def]
    _enforce(monkeypatch)
    org = owner(database, slug="quota-api-f", subject="quota-owner-f")
    set_quota(database, org, limit=1)
    provider.replies = [ok(INVALID), ok(VALID)]

    response = post(api_client, org, "quota-owner-f", "key-api-retry-01")

    assert response.status_code == 201 and response.json()["status"] == "succeeded"
    assert provider.calls == 2
    assert unit_counts(raw_engine, org, _current_period()) == (1, 0)


def test_a_fallback_answer_never_adds_a_unit(api_client, database, provider, monkeypatch, raw_engine):  # type: ignore[no-untyped-def]
    _enforce(monkeypatch)
    org = owner(database, slug="quota-api-g", subject="quota-owner-g")
    set_quota(database, org, limit=1)
    provider.replies = [httpx.ConnectError("simulated network failure"), ok(VALID)]

    response = post(api_client, org, "quota-owner-g", "key-api-fallback-1")

    assert response.status_code == 201 and response.json()["used_fallback"] is True
    assert provider.calls == 2
    assert unit_counts(raw_engine, org, _current_period()) == (1, 0)


def test_the_legacy_endpoint_is_retired_when_enforced(api_client, database, provider, monkeypatch):  # type: ignore[no-untyped-def]
    _enforce(monkeypatch)
    org = owner(database, slug="quota-api-h", subject="quota-owner-h")

    response = api_client.post(
        f"/api/v1/organizations/{org}/campaigns/analyze",
        json=BODY["data"],
        headers={"Authorization": f"Bearer {make_token('quota-owner-h')}"},
    )

    assert response.status_code == 410
    assert response.json()["detail"] == "ENDPOINT_RETIRED"
    assert provider.calls == 0


def _current_period() -> date:
    return quota_period_for(datetime.now(timezone.utc))


# --- operator procedure -------------------------------------------------------------------------------------------

@pytest.fixture
def cli_env(monkeypatch, test_database_url, database):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    return database


def test_the_operator_sets_and_replaces_an_organization_quota(cli_env, raw_engine):  # type: ignore[no-untyped-def]
    run(cli.create_organization("Cliente Cuota", "cliente-cuota"))

    assert cli.main(["set-analysis-quota", "--organization-slug", "cliente-cuota", "--plan", "pilot", "--monthly-limit", "50"]) == 0
    assert cli.main(["set-analysis-quota", "--organization-slug", "cliente-cuota", "--plan", "standard", "--monthly-limit", "80"]) == 0

    stored = rows(
        raw_engine,
        "SELECT q.plan, q.monthly_analysis_limit FROM analysis_quotas q"
        " JOIN organizations o ON o.id = q.organization_id WHERE o.slug = 'cliente-cuota'",
    )
    assert stored == [{"plan": "standard", "monthly_analysis_limit": 80}]


def test_the_operator_refuses_unknown_plans_and_negative_limits(cli_env):  # type: ignore[no-untyped-def]
    run(cli.create_organization("Cliente Rechazado", "cliente-rechazado"))

    with pytest.raises(SystemExit):
        cli.main(["set-analysis-quota", "--organization-slug", "cliente-rechazado", "--plan", "gold", "--monthly-limit", "5"])
    with pytest.raises(SystemExit):
        cli.main(["set-analysis-quota", "--organization-slug", "cliente-rechazado", "--plan", "pilot", "--monthly-limit", "-1"])


# --- audit: idempotency, double close, tenant boundary and lock ordering ------------------------------------------

def attempt_count(raw_engine, analysis_id) -> int:  # type: ignore[no-untyped-def]
    return rows(raw_engine, "SELECT count(*) AS n FROM analysis_usage WHERE analysis_id = :id", {"id": analysis_id})[0]["n"]


def test_the_same_key_sent_twice_at_once_is_counted_once(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-same", org_slug="quota-same")
    set_quota(database, org, limit=5)

    async def race():  # type: ignore[no-untyped-def]
        return await asyncio.gather(
            _claim(database, org=org, user=user, key="key-same-quota-1"),
            _claim(database, org=org, user=user, key="key-same-quota-1"),
            return_exceptions=True,
        )

    outcomes = sorted(r.outcome for r in run(race()))
    assert outcomes == ["claimed", "existing"]
    assert unit_counts(raw_engine, org) == (1, 0)


def test_a_replay_in_a_new_month_consumes_nothing_in_that_month(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-replay-m", org_slug="quota-replay-m")
    set_quota(database, org, limit=2)
    claim(database, org=org, user=user, key="key-replay-month-1", period=PERIOD)

    replay = claim(database, org=org, user=user, key="key-replay-month-1", period=NEXT_PERIOD)

    assert replay.outcome == "existing"
    assert unit_counts(raw_engine, org, PERIOD) == (1, 0)
    assert unit_counts(raw_engine, org, NEXT_PERIOD) == (0, 0)


def test_a_failed_analysis_cannot_be_closed_or_released_twice(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="quota-twice", org_slug="quota-twice")
    set_quota(database, org, limit=1)
    first = claim(database, org=org, user=user, key="key-twice-000001")
    run(ledger.finalize_analysis(
        database, organization_id=org, analysis_id=first.analysis_id, status="failed", error_code="AI_NOT_CONFIGURED",
    ))

    with pytest.raises(DBAPIError):
        run(ledger.finalize_analysis(
            database, organization_id=org, analysis_id=first.analysis_id, status="failed", error_code="AI_NOT_CONFIGURED",
        ))
    assert run(ledger.abandon_expired(database, organization_id=org, analysis_id=first.analysis_id)) is False

    assert unit_counts(raw_engine, org) == (0, 1)


def test_an_attempt_cannot_be_reserved_under_another_organization(database, raw_engine):  # type: ignore[no-untyped-def]
    org_a, user_a = seed(database, role=None, subject="quota-tenant-a", org_slug="quota-tenant-a")
    org_b, _ = seed(database, role=None, subject="quota-tenant-b", org_slug="quota-tenant-b")
    set_quota(database, org_a, limit=1)
    first = claim(database, org=org_a, user=user_a, key="key-tenant-00001")

    with pytest.raises(DBAPIError):
        run(ledger.reserve_attempt(
            database, organization_id=org_b, analysis_id=first.analysis_id, attempt_number=1,
            step="initial", model_role="primary", model=PRIMARY,
        ))

    assert attempt_count(raw_engine, first.analysis_id) == 0


def test_a_reservation_waits_for_a_closing_transaction_on_the_same_analysis(database, raw_engine):  # type: ignore[no-untyped-def]
    # Finalizing locks the analysis row before it counts attempts. A reservation takes a key-share lock on that row
    # through its foreign key, so it must wait: no call can be reserved between the count and the release.
    org, user = seed(database, role=None, subject="quota-lock", org_slug="quota-lock")
    set_quota(database, org, limit=1)
    first = claim(database, org=org, user=user, key="key-lock-0000001")

    async def scenario():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as closing:
            await closing.execute(
                text("SELECT id FROM analyses WHERE id = :id FOR UPDATE"), {"id": first.analysis_id}
            )
            reserving = asyncio.create_task(ledger.reserve_attempt(
                database, organization_id=org, analysis_id=first.analysis_id, attempt_number=1,
                step="initial", model_role="primary", model=PRIMARY,
            ))
            await asyncio.sleep(0.5)
            waited = not reserving.done()
            await closing.rollback()
        await reserving
        return waited

    assert run(scenario()) is True
    assert attempt_count(raw_engine, first.analysis_id) == 1
