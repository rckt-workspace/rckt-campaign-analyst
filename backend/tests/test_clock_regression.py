"""Regression tests for completion timestamps and history order. Deterministic: no test depends on the host clock.

Background (docs/PHASE2_PROVIDER_USAGE.md, section 8): the host clock can step backwards by about half a second, and
every PostgreSQL clock (now(), statement_timestamp(), clock_timestamp()) follows it. A row created in one transaction
and completed in a later one can therefore read completed_at < created_at. The rows below are written with explicit
creation times, ahead of the clock, to reproduce that order on demand.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import analysis_ledger as ledger
from app.services import analysis_history
from tests.conftest import run
from tests.test_analysis_ledger import PRIMARY, REPORT, rows
from tests.test_campaign_api import seed

HASH = "a" * 64
AHEAD = timedelta(hours=1)


def _future() -> datetime:
    return datetime.now(timezone.utc) + AHEAD


async def _execute(raw_engine, sql: str, params: dict) -> None:  # type: ignore[no-untyped-def]
    async with raw_engine.connect() as connection:
        await connection.execute(text(sql), params)
        await connection.commit()


def test_an_attempt_created_ahead_of_the_clock_completes_at_its_creation(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="clock-attempt", org_slug="clock-attempt")
    claim = run(ledger.claim_analysis(
        database, organization_id=org, requested_by=user, campaign_id=None, metric_entry_id=None,
        idempotency_key="key-clock-attempt-1", input_hash=HASH, prompt_version="analysis-v1", model_requested=PRIMARY,
        deterministic_report=REPORT, lease_seconds=120,
    ))
    created = _future()
    run(_execute(raw_engine,
        "INSERT INTO analysis_usage (organization_id, analysis_id, attempt_number, step, model_role, model, status,"
        " usage_reported, created_at) VALUES (:org, :analysis, 1, 'initial', 'primary', :model, 'reserved', false, :created)",
        {"org": org, "analysis": claim.analysis_id, "model": PRIMARY, "created": created}))
    attempt_id = rows(raw_engine, "SELECT id FROM analysis_usage WHERE analysis_id = :id", {"id": claim.analysis_id})[0]["id"]

    run(ledger.complete_attempt(database, organization_id=org, attempt_id=attempt_id, status="failed",
                                error_code="AI_HTTP_ERROR", http_status=500))

    stored = rows(raw_engine, "SELECT created_at, completed_at FROM analysis_usage WHERE id = :id", {"id": attempt_id})[0]
    assert stored["completed_at"] >= stored["created_at"]
    assert stored["completed_at"] == stored["created_at"]


def test_an_analysis_created_ahead_of_the_clock_finalizes_at_its_creation(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="clock-analysis", org_slug="clock-analysis")
    analysis_id = uuid.uuid4()
    created = _future()
    run(_execute(raw_engine,
        "INSERT INTO analyses (id, organization_id, requested_by, idempotency_key, input_hash, prompt_version,"
        " model_requested, deterministic_report, status, lease_expires_at, used_fallback, created_at)"
        " VALUES (:id, :org, :user, 'key-clock-analysis-1', :hash, 'analysis-v1', :model,"
        " CAST(:report AS jsonb), 'running', :lease, false, :created)",
        {"id": analysis_id, "org": org, "user": user, "hash": HASH, "model": PRIMARY,
         "report": json.dumps(REPORT), "lease": created + timedelta(minutes=5), "created": created}))

    run(ledger.finalize_analysis(database, organization_id=org, analysis_id=analysis_id, status="failed",
                                 error_code="AI_NOT_CONFIGURED"))

    stored = rows(raw_engine, "SELECT created_at, completed_at FROM analyses WHERE id = :id", {"id": analysis_id})[0]
    assert stored["completed_at"] == stored["created_at"]


def test_history_is_ordered_by_recorded_time_then_id_whatever_the_insertion_order(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="clock-history", org_slug="clock-history")
    base = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    # Inserted out of time order on purpose: the newest row is inserted second.
    times = {"oldest": base, "middle": base + timedelta(minutes=5), "newest": base + timedelta(minutes=9)}
    ids = {}
    for label in ("newest", "oldest", "middle"):
        ids[label] = uuid.uuid4()
        run(_execute(raw_engine,
            "INSERT INTO analyses (id, organization_id, requested_by, idempotency_key, input_hash, prompt_version,"
            " model_requested, deterministic_report, status, error_code, used_fallback, created_at, completed_at)"
            " VALUES (:id, :org, :user, :key, :hash, 'analysis-v1', :model, CAST(:report AS jsonb), 'failed',"
            " 'INTERNAL_ERROR', false, :created, :created)",
            {"id": ids[label], "org": org, "user": user, "key": f"key-history-{label}", "hash": HASH,
             "model": PRIMARY, "report": json.dumps(REPORT), "created": times[label]}))

    items, has_more = run(analysis_history.list_analyses(database, organization_id=org, limit=10, offset=0))

    assert [item["id"] for item in items] == [ids["newest"], ids["middle"], ids["oldest"]]
    assert has_more is False


def test_equal_recorded_times_are_ordered_by_id_descending(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="clock-tie", org_slug="clock-tie")
    same = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    ids = sorted(uuid.uuid4() for _ in range(3))
    for number, analysis_id in enumerate(ids):
        run(_execute(raw_engine,
            "INSERT INTO analyses (id, organization_id, requested_by, idempotency_key, input_hash, prompt_version,"
            " model_requested, deterministic_report, status, error_code, used_fallback, created_at, completed_at)"
            " VALUES (:id, :org, :user, :key, :hash, 'analysis-v1', :model, CAST(:report AS jsonb), 'failed',"
            " 'INTERNAL_ERROR', false, :created, :created)",
            {"id": analysis_id, "org": org, "user": user, "key": f"key-tie-{number}", "hash": HASH,
             "model": PRIMARY, "report": json.dumps(REPORT), "created": same}))

    items, _ = run(analysis_history.list_analyses(database, organization_id=org, limit=10, offset=0))

    assert [item["id"] for item in items] == list(reversed(ids))
