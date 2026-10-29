"""Quota clock: the month of a claim and its created_at come from one PostgreSQL instant, converted to UTC in the database.

Runs against the test database. No test reaches a provider except the simulated one used by test_analysis_api. The clock
and its reasons: app/db/clock.py and docs/PHASE2_QUOTA_CLOCK.md. The boundary cases evaluate the same SQL expression the
production claim uses, on instants chosen here, because the database clock itself cannot be moved from a test.
"""

import asyncio
import inspect
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import Date, DateTime, bindparam, cast, func, select, text
from sqlalchemy.exc import DBAPIError

from app.api import usage_routes
from app.db import analysis_ledger as ledger
from app.db import clock
from app.domain.quota import QuotaExceededError
from app.services import analysis_history
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import make_token
from tests.conftest import run
from tests.test_analysis_api import BODY, owner, post, provider  # noqa: F401  (pytest fixtures)
from tests.test_analysis_ledger import rows
from tests.test_campaign_api import seed
from tests.test_quota import _claim_now, analysis_count, db_period, set_quota, unit_counts

UTC = timezone.utc
AS_TIMESTAMP = DateTime(timezone=True)


# --- helpers: the database evaluates the same expression as the claim, on a given instant -------------------------

def period_in_zone(raw_engine, instant: datetime, zone: str) -> date:  # type: ignore[no-untyped-def]
    """The quota period the claim would assign to this instant, with the session on the given time zone."""

    async def read():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            await connection.execute(text("SELECT set_config('TimeZone', :zone, false)"), {"zone": zone})
            statement = select(clock.utc_quota_period(bindparam("instant", type_=AS_TIMESTAMP)))
            return (await connection.execute(statement, {"instant": instant})).scalar_one()

    return run(read())


def naive_month_in_zone(raw_engine, instant: datetime, zone: str) -> date:  # type: ignore[no-untyped-def]
    """The month of the instant read in the session zone, without the UTC conversion. Used only to show the difference."""

    async def read():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            await connection.execute(text("SELECT set_config('TimeZone', :zone, false)"), {"zone": zone})
            month = func.date_trunc("month", bindparam("instant", type_=AS_TIMESTAMP))
            return (await connection.execute(select(cast(month, Date)), {"instant": instant})).scalar_one()

    return run(read())


def stored_analysis(raw_engine, analysis_id):  # type: ignore[no-untyped-def]
    found = rows(
        raw_engine,
        "SELECT created_at, quota_period_start,"
        " (date_trunc('month', timezone('UTC', created_at)))::date AS month_of_created_at"
        " FROM analyses WHERE id = :id",
        {"id": analysis_id},
    )
    return found[0]


# --- 1 and 2: the period is the UTC month of the instant, computed by PostgreSQL -----------------------------------

@pytest.mark.parametrize(
    "instant, expected",
    [
        (datetime(2030, 10, 31, 23, 59, 59, 999999, tzinfo=UTC), date(2030, 10, 1)),
        (datetime(2030, 11, 1, 0, 0, 0, tzinfo=UTC), date(2030, 11, 1)),
        (datetime(2030, 12, 1, 0, 0, 0, tzinfo=UTC), date(2030, 12, 1)),
        # December closes into January: the same local wall clock is in January only after the UTC conversion.
        (datetime(2030, 12, 31, 23, 59, 59, 999999, tzinfo=UTC), date(2030, 12, 1)),
        (datetime(2030, 12, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5))), date(2031, 1, 1)),
        (datetime(2031, 1, 1, 0, 0, 0, tzinfo=UTC), date(2031, 1, 1)),
    ],
)
def test_the_period_is_the_utc_month_at_every_boundary(raw_engine, instant, expected):  # type: ignore[no-untyped-def]
    assert period_in_zone(raw_engine, instant, "UTC") == expected


def test_the_month_is_read_from_postgresql_not_from_the_application(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="clock-db", org_slug="clock-db")
    set_quota(database, org, limit=2)

    result = run(_claim_now(database, org=org, user=user, key="key-clock-db-01"))

    stored = stored_analysis(raw_engine, result.analysis_id)
    assert stored["quota_period_start"] == db_period(raw_engine)
    assert unit_counts(raw_engine, org, db_period(raw_engine)) == (1, 0)


# --- 3: the Python clock is not read on the month path --------------------------------------------------------------

def test_no_module_on_the_month_path_reads_the_python_clock():
    forbidden = ("datetime.now(", "datetime.utcnow(", "date.today(", "time.time(")
    for module in (clock, ledger, analysis_history, usage_routes):
        source = inspect.getsource(module)
        for call in forbidden:
            assert call not in source, f"{module.__name__} reads the Python clock through {call}"


# --- 4: the session time zone does not move the month ----------------------------------------------------------------

def test_the_session_time_zone_does_not_move_the_utc_month(raw_engine):  # type: ignore[no-untyped-def]
    # 23:30 UTC on 31 October is already 1 November in Kiritimati (UTC+14).
    instant = datetime(2030, 10, 31, 23, 30, tzinfo=UTC)

    assert period_in_zone(raw_engine, instant, "Pacific/Kiritimati") == date(2030, 10, 1)
    assert period_in_zone(raw_engine, instant, "America/Adak") == date(2030, 10, 1)
    # The conversion is what matters: without it, the same instant falls in November in that zone.
    assert naive_month_in_zone(raw_engine, instant, "Pacific/Kiritimati") == date(2030, 11, 1)


# --- 5 and 6: created_at and quota_period_start are the same instant, and replays consume nothing ------------------

def test_the_claim_month_and_created_at_describe_the_same_instant(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="clock-same", org_slug="clock-same")
    set_quota(database, org, limit=2)

    result = run(_claim_now(database, org=org, user=user, key="key-clock-same-1"))

    stored = stored_analysis(raw_engine, result.analysis_id)
    assert stored["quota_period_start"] == stored["month_of_created_at"]


def test_a_replay_keeps_the_original_month_and_consumes_nothing(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="clock-replay", org_slug="clock-replay")
    set_quota(database, org, limit=2)

    first = run(_claim_now(database, org=org, user=user, key="key-clock-replay-1"))
    replay = run(_claim_now(database, org=org, user=user, key="key-clock-replay-1"))

    assert first.outcome == "claimed"
    assert replay.outcome == "existing"
    assert replay.analysis_id == first.analysis_id
    assert analysis_count(raw_engine, org) == 1
    assert unit_counts(raw_engine, org, db_period(raw_engine)) == (1, 0)


# --- 7: concurrency with the last unit ------------------------------------------------------------------------------

def test_two_concurrent_claims_on_the_database_clock_admit_exactly_one(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="clock-race", org_slug="clock-race")
    set_quota(database, org, limit=1)

    async def race():  # type: ignore[no-untyped-def]
        return await asyncio.gather(
            _claim_now(database, org=org, user=user, key="key-clock-race-1"),
            _claim_now(database, org=org, user=user, key="key-clock-race-2"),
            return_exceptions=True,
        )

    results = run(race())

    assert sum(1 for item in results if getattr(item, "outcome", None) == "claimed") == 1
    assert sum(1 for item in results if isinstance(item, QuotaExceededError)) == 1
    assert analysis_count(raw_engine, org) == 1
    assert unit_counts(raw_engine, org, db_period(raw_engine)) == (1, 0)


# --- 8: exhausted quota, through HTTP, never reaches the provider ----------------------------------------------------

def test_an_exhausted_quota_on_the_database_month_answers_429_without_a_provider_call(
    api_client, database, provider, monkeypatch, raw_engine,  # type: ignore[no-untyped-def]
):
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "true")
    org = owner(database, slug="clock-api-a", subject="clock-owner-a")
    set_quota(database, org, limit=0)

    response = post(api_client, org, "clock-owner-a", "key-clock-api-01")

    assert response.status_code == 429
    assert provider.calls == 0
    assert analysis_count(raw_engine, org) == 0


# --- 9: enforcement off still records the month, and refuses nothing ------------------------------------------------

def test_with_enforcement_off_the_unit_is_counted_in_the_database_month(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="clock-off", org_slug="clock-off")

    result = run(_claim_now(database, org=org, user=user, key="key-clock-off-01", enforce=False))

    assert result.outcome == "claimed"
    assert unit_counts(raw_engine, org, db_period(raw_engine)) == (1, 0)


# --- 10: a failed transaction leaves neither the analysis nor the unit ----------------------------------------------

def test_a_failure_after_the_month_is_read_rolls_back_the_analysis_and_the_unit(database, raw_engine, monkeypatch):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="clock-rollback", org_slug="clock-rollback")
    set_quota(database, org, limit=2)

    async def broken(session, **kwargs):  # type: ignore[no-untyped-def]
        raise DBAPIError("UPDATE analysis_quota_usage", None, Exception("injected failure"))

    monkeypatch.setattr(ledger, "_consume_unit", broken)
    with pytest.raises(DBAPIError):
        run(_claim_now(database, org=org, user=user, key="key-clock-rollback-1"))

    assert analysis_count(raw_engine, org) == 0
    assert unit_counts(raw_engine, org, db_period(raw_engine)) is None


# --- 11: the monthly report and the audit trail use the same month --------------------------------------------------

def test_the_audit_event_and_the_monthly_report_carry_the_database_month(
    api_client, database, raw_engine, monkeypatch,  # type: ignore[no-untyped-def]
):
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "true")
    org = owner(database, slug="clock-report", subject="clock-owner-r")
    set_quota(database, org, limit=5)
    response = post(api_client, org, "clock-owner-r", "key-clock-report-1")
    assert response.status_code == 201

    month = db_period(raw_engine)
    label = f"{month.year:04d}-{month.month:02d}"
    audited = rows(
        raw_engine,
        "SELECT metadata->>'quota_period' AS quota_period FROM audit_events"
        " WHERE organization_id = :org AND action = 'analysis.created'",
        {"org": org},
    )
    assert [row["quota_period"] for row in audited] == [label]

    headers = {"Authorization": f"Bearer {make_token('clock-owner-r')}"}
    report = api_client.get(f"/api/v1/organizations/{org}/usage", headers=headers)
    assert report.status_code == 200
    body = report.json()
    assert body["period"] == label
    assert body["analyses"]["requested"] == 1
