"""Analysis history schema: constraints, isolation, idempotency, lease and immutability rules, checked with direct SQL.

The protocol itself (claim, reserve, complete, finalize, abandon) is tested in test_analysis_ledger.py. Runs only on
the migrated test database (rckt_test).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.repositories import OrganizationRepository, UserRepository
from tests.conftest import alembic_config, run

HASH = "a" * 64
REPORT = '{"investment": 1}'
LIVE = timedelta(hours=1)
LATER = timedelta(minutes=5)

INSERT_ANALYSIS = """
INSERT INTO analyses (
  id, organization_id, campaign_id, metric_entry_id, requested_by, idempotency_key, input_hash,
  prompt_version, model_requested, deterministic_report, status, lease_expires_at, error_code,
  analysis_markdown, model_used, format_valid, completed_at
) VALUES (
  :id, :org, :campaign, :entry, :user, :key, :hash,
  'analysis-v1', 'model-primary', CAST(:report AS jsonb), :status, :lease, :error,
  :markdown, :model_used, :format_valid, :completed
)
"""

INSERT_ATTEMPT = """
INSERT INTO analysis_usage (
  id, organization_id, analysis_id, attempt_number, step, model_role, model, status, error_code,
  http_status, usage_reported, prompt_tokens, completion_tokens, cost_micros, completed_at
) VALUES (
  :id, :org, :analysis, :number, :step, :role, 'model-primary', :status, :error,
  :http, :reported, :prompt_tokens, :completion_tokens, :cost_micros, :completed
)
"""

ATTEMPT_DEFAULTS = {
    "number": 1, "step": "initial", "role": "primary", "status": "reserved", "error": None, "http": None,
    "reported": False, "prompt_tokens": None, "completion_tokens": None, "cost_micros": None, "completed": None,
}


async def _execute(raw_engine, sql: str, params: dict) -> None:  # type: ignore[no-untyped-def]
    async with raw_engine.begin() as connection:
        await connection.execute(text(sql), params)


def execute(raw_engine, sql: str, params: dict | None = None) -> None:  # type: ignore[no-untyped-def]
    run(_execute(raw_engine, sql, params or {}))


def _at(value):  # type: ignore[no-untyped-def]
    """Durations in the cases below are relative to now; the database receives absolute timestamps."""
    return datetime.now(UTC) + value if isinstance(value, timedelta) else value


def analysis_values(seeded, organization, **overrides) -> dict:  # type: ignore[no-untyped-def]
    values = {
        "id": uuid.uuid4(), "org": organization, "campaign": None, "entry": None, "user": seeded["user"],
        "key": f"key-{uuid.uuid4()}", "hash": HASH, "report": REPORT, "status": "running",
        "lease": datetime.now(UTC) + LIVE, "error": None, "markdown": None, "model_used": None,
        "format_valid": None, "completed": None,
    }
    values.update(overrides)
    return values


def insert_analysis(raw_engine, seeded, organization, **overrides) -> dict:  # type: ignore[no-untyped-def]
    values = analysis_values(seeded, organization, **overrides)
    values["lease"] = _at(values["lease"])
    values["completed"] = _at(values["completed"])
    execute(raw_engine, INSERT_ANALYSIS, values)
    return values


def insert_attempt(raw_engine, seeded, analysis_id, organization, **overrides) -> dict:  # type: ignore[no-untyped-def]
    values = {**ATTEMPT_DEFAULTS, "id": uuid.uuid4(), "org": organization, "analysis": analysis_id}
    values.update(overrides)
    values["completed"] = _at(values["completed"])
    execute(raw_engine, INSERT_ATTEMPT, values)
    return values


@pytest.fixture
def seeded(database, raw_engine):  # type: ignore[no-untyped-def]
    """Two organizations, one user, and in organization A two campaigns with one metric entry each."""

    async def create():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            org_a = await OrganizationRepository(session).create(name="Cliente A", slug="cliente-a")
            org_b = await OrganizationRepository(session).create(name="Cliente B", slug="cliente-b")
            user = await UserRepository(session).create(external_subject="schema-user")
            return org_a.id, org_b.id, user.id

    org_a, org_b, user = run(create())
    campaign, campaign2 = uuid.uuid4(), uuid.uuid4()
    entry, entry2 = uuid.uuid4(), uuid.uuid4()
    for identifier in (campaign, campaign2):
        execute(raw_engine, "INSERT INTO campaigns (id, organization_id, name) VALUES (:id, :org, 'Campaña')",
                {"id": identifier, "org": org_a})
    for identifier, parent in ((entry, campaign), (entry2, campaign2)):
        execute(raw_engine,
                "INSERT INTO campaign_metric_entries (id, organization_id, campaign_id, source, raw_input, investment)"
                " VALUES (:id, :org, :campaign, 'form', 'x', 100)",
                {"id": identifier, "org": org_a, "campaign": parent})
    return {"org_a": org_a, "org_b": org_b, "user": user, "campaign": campaign, "campaign2": campaign2,
            "entry": entry, "entry2": entry2}


# --- structure ---------------------------------------------------------------------------------------------------

def test_new_tables_have_row_level_security_and_no_policy_for_other_roles(raw_engine):  # type: ignore[no-untyped-def]
    async def inspect_tables():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            flags = await connection.execute(
                text("SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('analyses', 'analysis_usage')")
            )
            # Migration 0009 adds policies for rckt_app only. Any policy for another role is a defect.
            policies = await connection.execute(
                text(
                    "SELECT count(*) FROM pg_policies WHERE tablename IN ('analyses', 'analysis_usage')"
                    " AND roles IS DISTINCT FROM ARRAY['rckt_app']::name[]"
                )
            )
            grants = await connection.execute(text(
                "SELECT count(*) FROM information_schema.role_table_grants"
                " WHERE table_name IN ('analyses', 'analysis_usage') AND grantee IN ('anon', 'authenticated')"
            ))
            return dict(flags.all()), policies.scalar_one(), grants.scalar_one()

    flags, policy_count, anon_grants = run(inspect_tables())
    assert flags == {"analyses": True, "analysis_usage": True}
    assert policy_count == 0
    assert anon_grants == 0


def test_migration_downgrade_and_upgrade_round_trip(test_database_url, raw_engine):  # type: ignore[no-untyped-def]
    command.downgrade(alembic_config(), "0003_campaigns")

    async def tables():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            return await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))

    assert "analyses" not in run(tables())
    command.upgrade(alembic_config(), "head")
    assert {"analyses", "analysis_usage"} <= run(tables())


# --- isolation -------------------------------------------------------------------------------------------------

def test_an_analysis_cannot_use_another_organizations_campaign(seeded, raw_engine):  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError, match="fk_analyses_campaign"):
        insert_analysis(raw_engine, seeded, seeded["org_b"], campaign=seeded["campaign"])


def test_a_metric_entry_must_belong_to_the_campaign_it_is_recorded_with(seeded, raw_engine):  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError, match="fk_analyses_metric_entry"):
        insert_analysis(raw_engine, seeded, seeded["org_a"], campaign=seeded["campaign"], entry=seeded["entry2"])


def test_a_metric_entry_without_a_campaign_is_refused(seeded, raw_engine):  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError, match="ck_analyses_metric_entry_needs_campaign"):
        insert_analysis(raw_engine, seeded, seeded["org_a"], entry=seeded["entry"])


def test_an_attempt_cannot_be_written_into_another_organizations_analysis(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    with pytest.raises(DBAPIError):
        insert_attempt(raw_engine, seeded, analysis["id"], seeded["org_b"])


def test_the_idempotency_key_is_unique_within_an_organization_only(seeded, raw_engine):  # type: ignore[no-untyped-def]
    insert_analysis(raw_engine, seeded, seeded["org_a"], key="igual")
    with pytest.raises(IntegrityError, match="uq_analyses_organization_id_idempotency_key"):
        insert_analysis(raw_engine, seeded, seeded["org_a"], key="igual")
    insert_analysis(raw_engine, seeded, seeded["org_b"], key="igual")


def test_eight_simultaneous_inserts_with_one_key_leave_exactly_one_row(seeded, raw_engine):  # type: ignore[no-untyped-def]
    key = f"carrera-{uuid.uuid4()}"

    async def insert_once():  # type: ignore[no-untyped-def]
        values = analysis_values(seeded, seeded["org_a"], key=key)
        async with raw_engine.begin() as connection:
            result = await connection.execute(
                text(INSERT_ANALYSIS + " ON CONFLICT (organization_id, idempotency_key) DO NOTHING RETURNING id"),
                values,
            )
            return result.scalar_one_or_none()

    async def race():  # type: ignore[no-untyped-def]
        return await asyncio.gather(*(insert_once() for _ in range(8)))

    assert sum(1 for winner in run(race()) if winner is not None) == 1


# --- states and formats ----------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"status": "done"}, "ck_analyses_status_valid"),
        ({"hash": "ABC"}, "ck_analyses_input_hash_format"),
        ({"report": "[1]"}, "ck_analyses_report_is_object"),
        ({"status": "running", "lease": None}, "ck_analyses_running_state"),
        ({"status": "running", "error": "AI_TIMEOUT"}, "ck_analyses_running_state"),
        ({"status": "succeeded", "lease": None, "completed": LATER, "model_used": "m", "format_valid": True},
         "ck_analyses_succeeded_state"),
        ({"status": "succeeded", "lease": None, "completed": LATER, "markdown": "x", "format_valid": True},
         "ck_analyses_succeeded_state"),
        ({"status": "succeeded", "lease": None, "completed": LATER, "markdown": "x", "model_used": "m"},
         "ck_analyses_succeeded_state"),
        ({"status": "succeeded", "lease": None, "completed": LATER, "markdown": "x", "model_used": "m",
          "format_valid": False}, "ck_analyses_succeeded_state"),
        ({"status": "succeeded", "lease": None, "completed": LATER, "markdown": "x" * 100_001, "model_used": "m",
          "format_valid": True}, "ck_analyses_markdown_length"),
        ({"status": "failed", "lease": None, "completed": LATER, "error": None}, "ck_analyses_failed_state"),
        ({"status": "failed", "lease": None, "completed": LATER, "error": "SECRET_PROVIDER_BODY"},
         "ck_analyses_error_code_valid"),
    ],
)
def test_invalid_states_and_formats_are_refused(seeded, raw_engine, overrides, constraint):  # type: ignore[no-untyped-def]
    with pytest.raises((IntegrityError, DBAPIError), match=constraint):
        insert_analysis(raw_engine, seeded, seeded["org_a"], **overrides)


def test_every_analysis_stores_its_deterministic_report(seeded, raw_engine):  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError, match="deterministic_report"):
        insert_analysis(raw_engine, seeded, seeded["org_a"], report=None)


# --- analysis immutability -----------------------------------------------------------------------------------------

def test_a_completed_analysis_cannot_be_changed_or_deleted(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    attempt = insert_attempt(raw_engine, seeded, analysis["id"], seeded["org_a"])
    execute(raw_engine, "UPDATE analysis_usage SET status = 'succeeded', completed_at = now() WHERE id = :id",
            {"id": attempt["id"]})
    execute(raw_engine, "UPDATE analyses SET status = 'succeeded', lease_expires_at = NULL, completed_at = now(),"
                        " analysis_markdown = 'Informe', model_used = 'model-primary', format_valid = true"
                        " WHERE id = :id", {"id": analysis["id"]})

    with pytest.raises(DBAPIError, match="append-only"):
        execute(raw_engine, "UPDATE analyses SET analysis_markdown = 'otro' WHERE id = :id", {"id": analysis["id"]})
    with pytest.raises(DBAPIError, match="append-only"):
        execute(raw_engine, "DELETE FROM analyses WHERE id = :id", {"id": analysis["id"]})


def test_a_running_analysis_keeps_its_inputs(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    with pytest.raises(DBAPIError, match="inputs of an analysis are immutable"):
        execute(raw_engine, "UPDATE analyses SET input_hash = :hash WHERE id = :id",
                {"hash": "b" * 64, "id": analysis["id"]})
    with pytest.raises(DBAPIError, match="inputs of an analysis are immutable"):
        execute(raw_engine, "UPDATE analyses SET model_requested = 'otro' WHERE id = :id", {"id": analysis["id"]})


def test_an_analysis_cannot_be_deleted_while_running(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    with pytest.raises(DBAPIError, match="DELETE is not allowed"):
        execute(raw_engine, "DELETE FROM analyses WHERE id = :id", {"id": analysis["id"]})


# --- attempts: constraints ------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        # BEFORE INSERT triggers run before CHECK constraints: an invalid step or role on the first attempt is
        # refused by the sequence rule first. Either message proves the refusal.
        ({"step": "retry"}, "ck_analysis_usage_step_valid|first attempt must be the primary"),
        ({"role": "tertiary"}, "ck_analysis_usage_model_role_valid|first attempt must be the primary"),
        ({"status": "pending"}, "ck_analysis_usage_status_valid|ck_analysis_usage_state"),
        ({"error": "RAW_PROVIDER_TEXT", "status": "failed", "completed": LATER}, "ck_analysis_usage_error_code_valid"),
        ({"error": "AI_HTTP_ERROR", "status": "failed", "completed": LATER, "http": 999},
         "ck_analysis_usage_http_status_range"),
        ({"status": "reserved", "error": "AI_TIMEOUT"}, "ck_analysis_usage_state"),
        ({"status": "unknown", "error": None, "completed": LATER}, "ck_analysis_usage_state"),
        ({"status": "succeeded", "completed": LATER, "prompt_tokens": 10}, "ck_analysis_usage_reported_values"),
        ({"status": "succeeded", "completed": LATER, "reported": True, "prompt_tokens": -1},
         "ck_analysis_usage_values_non_negative"),
    ],
)
def test_attempt_constraints_are_enforced(seeded, raw_engine, overrides, constraint):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"], lease=datetime.now(UTC) + LIVE)
    with pytest.raises((IntegrityError, DBAPIError), match=constraint):
        insert_attempt(raw_engine, seeded, analysis["id"], seeded["org_a"], **overrides)


def test_provider_tokens_are_stored_exactly_when_reported(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    insert_attempt(raw_engine, seeded, analysis["id"], seeded["org_a"], status="succeeded", completed=LATER,
                   reported=True, prompt_tokens=812, completion_tokens=430, cost_micros=1875)
    row = run(_read(raw_engine, "SELECT prompt_tokens, completion_tokens, cost_micros FROM analysis_usage"
                                " WHERE analysis_id = :id", {"id": analysis["id"]}))
    assert tuple(row) == (812, 430, 1875)


async def _read(raw_engine, sql: str, params: dict):  # type: ignore[no-untyped-def]
    async with raw_engine.connect() as connection:
        return (await connection.execute(text(sql), params)).one()


# --- attempts: append-only ------------------------------------------------------------------------------------------

def test_a_reserved_attempt_completes_once_and_a_recorded_attempt_is_immutable(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    attempt = insert_attempt(raw_engine, seeded, analysis["id"], seeded["org_a"])
    execute(raw_engine, "UPDATE analysis_usage SET status = 'failed', error_code = 'AI_NETWORK',"
                        " completed_at = now() WHERE id = :id", {"id": attempt["id"]})

    with pytest.raises(DBAPIError, match="recorded attempt is immutable"):
        execute(raw_engine, "UPDATE analysis_usage SET status = 'succeeded' WHERE id = :id", {"id": attempt["id"]})
    with pytest.raises(DBAPIError, match="append-only"):
        execute(raw_engine, "DELETE FROM analysis_usage WHERE id = :id", {"id": attempt["id"]})


def test_an_attempt_identity_cannot_be_rewritten_while_reserved(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    attempt = insert_attempt(raw_engine, seeded, analysis["id"], seeded["org_a"])
    with pytest.raises(DBAPIError, match="identity of an attempt is immutable"):
        execute(raw_engine, "UPDATE analysis_usage SET model = 'otro' WHERE id = :id", {"id": attempt["id"]})
