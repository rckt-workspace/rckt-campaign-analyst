"""Analysis history rules enforced by PostgreSQL: isolation, idempotency under concurrency, safe states, immutability,
and one recorded row per provider attempt. Runs only on the migrated test database (rckt_test).
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
FUTURE = datetime.now(UTC) + timedelta(hours=1)
LATER = datetime.now(UTC) + timedelta(minutes=5)

INSERT_ANALYSIS = """
INSERT INTO analyses (
  id, organization_id, campaign_id, metric_entry_id, requested_by, idempotency_key, input_hash, status,
  lease_expires_at, error_code, analysis_markdown, prompt_version, model_requested, model_used, completed_at
) VALUES (
  :id, :org, :campaign, :entry, :user, :key, :hash, :status,
  :lease, :error, :markdown, 'analysis-v1', 'model-primary', :model_used, :completed
)
"""

CLAIM_ANALYSIS = INSERT_ANALYSIS.rstrip() + " ON CONFLICT (organization_id, idempotency_key) DO NOTHING RETURNING id"

INSERT_USAGE = """
INSERT INTO analysis_usage (
  id, organization_id, analysis_id, attempt_kind, model, status, error_code,
  usage_reported, prompt_tokens, completion_tokens, cost_micros, completed_at
) VALUES (
  :id, :org, :analysis, :kind, 'model-primary', :status, :error,
  :reported, :prompt_tokens, :completion_tokens, :cost_micros, :completed
)
"""


def base_analysis(**overrides) -> dict:  # type: ignore[no-untyped-def]
    """A running analysis, as the claim inserts it. Overrides change one field at a time in tests."""
    values = {
        "id": uuid.uuid4(),
        "status": "running",
        "hash": HASH,
        "key": f"key-{uuid.uuid4()}",
        "lease": FUTURE,
        "error": None,
        "markdown": None,
        "completed": None,
        "model_used": None,
        "campaign": None,
        "entry": None,
    }
    values.update(overrides)
    return values


@pytest.fixture
def seeded(database, raw_engine):  # type: ignore[no-untyped-def]
    """Two organizations, one user, and in organization A two campaigns with one metric entry each."""

    async def create():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            org_a = await OrganizationRepository(session).create(name="Cliente A", slug="cliente-a")
            org_b = await OrganizationRepository(session).create(name="Cliente B", slug="cliente-b")
            user = await UserRepository(session).create(external_subject="analysis-user")
            return org_a.id, org_b.id, user.id

    org_a, org_b, user = run(create())

    async def execute(sql: str, params: dict) -> None:
        async with raw_engine.begin() as connection:
            await connection.execute(text(sql), params)

    campaign_a = uuid.uuid4()
    campaign_a2 = uuid.uuid4()
    entry_a = uuid.uuid4()
    entry_a2 = uuid.uuid4()
    for campaign in (campaign_a, campaign_a2):
        run(execute("INSERT INTO campaigns (id, organization_id, name) VALUES (:id, :org, 'Campaña')",
                    {"id": campaign, "org": org_a}))
    for entry, campaign in ((entry_a, campaign_a), (entry_a2, campaign_a2)):
        run(execute(
            "INSERT INTO campaign_metric_entries (id, organization_id, campaign_id, source, raw_input, investment)"
            " VALUES (:id, :org, :campaign, 'form', 'x', 100)",
            {"id": entry, "org": org_a, "campaign": campaign},
        ))
    return {
        "org_a": org_a,
        "org_b": org_b,
        "user": user,
        "campaign_a": campaign_a,
        "campaign_a2": campaign_a2,
        "entry_a": entry_a,
        "entry_a2": entry_a2,
    }


def insert_analysis(raw_engine, seeded, organization, **overrides) -> dict:  # type: ignore[no-untyped-def]
    values = base_analysis(**overrides)
    values.update({"org": organization, "user": seeded["user"]})
    run(_execute(raw_engine, INSERT_ANALYSIS, values))
    return values


async def _execute(raw_engine, sql: str, params: dict) -> None:  # type: ignore[no-untyped-def]
    async with raw_engine.begin() as connection:
        await connection.execute(text(sql), params)


def complete_as_succeeded(raw_engine, analysis_id, markdown: str = "## Estado general\nOK") -> None:  # type: ignore[no-untyped-def]
    run(
        _execute(
            raw_engine,
            "UPDATE analyses SET status = 'succeeded', lease_expires_at = NULL, completed_at = now(),"
            " analysis_markdown = :markdown, model_used = 'model-primary' WHERE id = :id",
            {"id": analysis_id, "markdown": markdown},
        )
    )


# --- structure and access -------------------------------------------------------------------------------------

def test_new_tables_have_row_level_security_and_no_policies(raw_engine):  # type: ignore[no-untyped-def]
    async def inspect_tables():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            enabled = await connection.execute(
                text("SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('analyses', 'analysis_usage')")
            )
            policies = await connection.execute(
                text("SELECT count(*) FROM pg_policies WHERE tablename IN ('analyses', 'analysis_usage')")
            )
            return dict(enabled.all()), policies.scalar_one()

    flags, policy_count = run(inspect_tables())
    assert flags == {"analyses": True, "analysis_usage": True}
    assert policy_count == 0


def test_migration_downgrade_and_upgrade_round_trip(test_database_url, raw_engine):  # type: ignore[no-untyped-def]
    command.downgrade(alembic_config(), "0003_campaigns")

    async def tables():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            return await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))

    assert "analyses" not in run(tables())
    command.upgrade(alembic_config(), "head")
    assert {"analyses", "analysis_usage"} <= run(tables())


# --- isolation between organizations -----------------------------------------------------------------------------

def test_an_analysis_cannot_use_another_organizations_campaign(seeded, raw_engine):  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError, match="fk_analyses_campaign"):
        insert_analysis(raw_engine, seeded, seeded["org_b"], campaign=seeded["campaign_a"])


def test_a_metric_entry_must_belong_to_the_campaign_it_is_recorded_with(seeded, raw_engine):  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError, match="fk_analyses_metric_entry"):
        insert_analysis(
            raw_engine, seeded, seeded["org_a"], campaign=seeded["campaign_a"], entry=seeded["entry_a2"]
        )


def test_a_metric_entry_without_a_campaign_is_refused(seeded, raw_engine):  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError, match="ck_analyses_metric_entry_needs_campaign"):
        insert_analysis(raw_engine, seeded, seeded["org_a"], entry=seeded["entry_a"])


def test_an_attempt_cannot_point_at_another_organizations_analysis(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    with pytest.raises(IntegrityError, match="fk_analysis_usage_analysis"):
        run(
            _execute(
                raw_engine,
                INSERT_USAGE,
                {
                    "id": uuid.uuid4(), "org": seeded["org_b"], "analysis": analysis["id"], "kind": "primary",
                    "status": "reserved", "error": None, "reported": False, "prompt_tokens": None,
                    "completion_tokens": None, "cost_micros": None, "completed": None,
                },
            )
        )


# --- idempotency ----------------------------------------------------------------------------------------------------

def test_an_idempotency_key_is_unique_within_an_organization_only(seeded, raw_engine):  # type: ignore[no-untyped-def]
    first = insert_analysis(raw_engine, seeded, seeded["org_a"], key="mismo-clave")
    assert first["key"] == "mismo-clave"
    with pytest.raises(IntegrityError, match="uq_analyses_organization_id_idempotency_key"):
        insert_analysis(raw_engine, seeded, seeded["org_a"], key="mismo-clave")
    insert_analysis(raw_engine, seeded, seeded["org_b"], key="mismo-clave")


def test_concurrent_claims_with_one_key_produce_one_row_and_one_winner(seeded, raw_engine):  # type: ignore[no-untyped-def]
    key = f"carrera-{uuid.uuid4()}"

    async def claim(attempt_id: uuid.UUID):  # type: ignore[no-untyped-def]
        values = base_analysis(id=attempt_id, key=key)
        values.update({"org": seeded["org_a"], "user": seeded["user"]})
        async with raw_engine.begin() as connection:
            result = await connection.execute(text(CLAIM_ANALYSIS), values)
            return result.scalar_one_or_none()

    async def race():  # type: ignore[no-untyped-def]
        return await asyncio.gather(*(claim(uuid.uuid4()) for _ in range(8)))

    winners = [row for row in run(race()) if row is not None]
    assert len(winners) == 1

    async def count():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            return (
                await connection.execute(
                    text("SELECT count(*) FROM analyses WHERE organization_id = :org AND idempotency_key = :key"),
                    {"org": seeded["org_a"], "key": key},
                )
            ).scalar_one()

    assert run(count()) == 1


# --- safe states and formats ----------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"status": "done"}, "ck_analyses_status_valid"),
        ({"hash": "ABC"}, "ck_analyses_input_hash_format"),
        ({"status": "running", "lease": None}, "ck_analyses_running_state"),
        (
            {"status": "succeeded", "lease": None, "completed": LATER, "model_used": "m", "markdown": None},
            "ck_analyses_succeeded_state",
        ),
        (
            {"status": "succeeded", "lease": None, "completed": LATER, "model_used": "m", "markdown": "x" * 100_001},
            "ck_analyses_markdown_length",
        ),
        ({"status": "failed", "lease": None, "completed": LATER, "error": None}, "ck_analyses_failed_state"),
        (
            {"status": "failed", "lease": None, "completed": LATER, "error": "SECRET_PROVIDER_BODY"},
            "ck_analyses_error_code_valid",
        ),
    ],
)
def test_invalid_states_and_formats_are_refused(seeded, raw_engine, overrides, constraint):  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError, match=constraint):
        insert_analysis(raw_engine, seeded, seeded["org_a"], **overrides)


def test_a_failed_analysis_stores_only_a_safe_code(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    run(
        _execute(
            raw_engine,
            "UPDATE analyses SET status = 'failed', lease_expires_at = NULL, completed_at = now(),"
            " error_code = 'AI_TIMEOUT' WHERE id = :id",
            {"id": analysis["id"]},
        )
    )


# --- immutability ----------------------------------------------------------------------------------------------------

def test_a_completed_analysis_cannot_be_changed_or_deleted(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    complete_as_succeeded(raw_engine, analysis["id"])

    with pytest.raises(DBAPIError, match="append-only"):
        run(_execute(raw_engine, "UPDATE analyses SET analysis_markdown = 'otro' WHERE id = :id", {"id": analysis["id"]}))
    with pytest.raises(DBAPIError, match="append-only"):
        run(_execute(raw_engine, "DELETE FROM analyses WHERE id = :id", {"id": analysis["id"]}))


def test_a_running_analysis_keeps_its_inputs_but_may_renew_its_lease(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    run(
        _execute(
            raw_engine,
            "UPDATE analyses SET lease_expires_at = now() + interval '2 minutes' WHERE id = :id",
            {"id": analysis["id"]},
        )
    )
    with pytest.raises(DBAPIError, match="inputs of an analysis are immutable"):
        run(_execute(raw_engine, "UPDATE analyses SET input_hash = :hash WHERE id = :id",
                     {"hash": "b" * 64, "id": analysis["id"]}))


# --- attempts ----------------------------------------------------------------------------------------------------------

def attempt(raw_engine, seeded, analysis_id, kind: str, **overrides) -> dict:  # type: ignore[no-untyped-def]
    values = {
        "id": uuid.uuid4(), "org": seeded["org_a"], "analysis": analysis_id, "kind": kind,
        "status": "reserved", "error": None, "reported": False, "prompt_tokens": None,
        "completion_tokens": None, "cost_micros": None, "completed": None,
    }
    values.update(overrides)
    run(_execute(raw_engine, INSERT_USAGE, values))
    return values


def test_each_attempt_kind_is_recorded_once_per_analysis(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    attempt(raw_engine, seeded, analysis["id"], "primary")
    with pytest.raises(IntegrityError, match="uq_analysis_usage_analysis_attempt"):
        attempt(raw_engine, seeded, analysis["id"], "primary")
    attempt(raw_engine, seeded, analysis["id"], "fallback")


def test_provider_tokens_are_stored_only_when_the_provider_reported_them(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    with pytest.raises(IntegrityError, match="ck_analysis_usage_reported_values"):
        attempt(raw_engine, seeded, analysis["id"], "primary", prompt_tokens=10)

    reported = attempt(
        raw_engine, seeded, analysis["id"], "fallback", status="succeeded", reported=True,
        prompt_tokens=10, completion_tokens=20, cost_micros=1500, completed=LATER,
    )
    assert reported["kind"] == "fallback"


def test_a_recorded_attempt_is_append_only(seeded, raw_engine):  # type: ignore[no-untyped-def]
    analysis = insert_analysis(raw_engine, seeded, seeded["org_a"])
    recorded = attempt(raw_engine, seeded, analysis["id"], "primary")
    run(_execute(
        raw_engine,
        "UPDATE analysis_usage SET status = 'failed', error_code = 'AI_UNAVAILABLE', completed_at = now()"
        " WHERE id = :id",
        {"id": recorded["id"]},
    ))

    with pytest.raises(DBAPIError, match="recorded attempt is immutable"):
        run(_execute(raw_engine, "UPDATE analysis_usage SET model = 'otro' WHERE id = :id", {"id": recorded["id"]}))
    with pytest.raises(DBAPIError, match="append-only"):
        run(_execute(raw_engine, "DELETE FROM analysis_usage WHERE id = :id", {"id": recorded["id"]}))
