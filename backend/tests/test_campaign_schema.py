"""Campaign persistence rules enforced by PostgreSQL itself, not only by the application."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.repositories import OrganizationRepository, UserRepository
from tests.conftest import run


def _seed_organizations(database) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    async def create():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            org_a = await OrganizationRepository(session).create(name="Cliente A", slug="cliente-a")
            org_b = await OrganizationRepository(session).create(name="Cliente B", slug="cliente-b")
            user = await UserRepository(session).create(external_subject="campaign-owner")
            return org_a.id, org_b.id, user.id

    return run(create())


async def _execute(raw_engine, sql: str, params: dict | None = None):  # type: ignore[no-untyped-def]
    async with raw_engine.begin() as connection:
        await connection.execute(text(sql), params or {})


async def _scalar(raw_engine, sql: str, params: dict | None = None):  # type: ignore[no-untyped-def]
    async with raw_engine.connect() as connection:
        return (await connection.execute(text(sql), params or {})).scalar_one()


def _insert_campaign(raw_engine, organization_id: uuid.UUID, name: str = "Campaña") -> uuid.UUID:  # type: ignore[no-untyped-def]
    campaign_id = uuid.uuid4()
    run(
        _execute(
            raw_engine,
            "INSERT INTO campaigns (id, organization_id, name) VALUES (:id, :org, :name)",
            {"id": campaign_id, "org": organization_id, "name": name},
        )
    )
    return campaign_id


ENTRY_SQL = (
    "INSERT INTO campaign_metric_entries (id, organization_id, campaign_id, source, raw_input, investment)"
    " VALUES (:id, :org, :campaign, 'form', 'Campaña: x', :investment)"
)


def test_metric_entry_cannot_reference_a_campaign_of_another_organization(database, raw_engine):  # type: ignore[no-untyped-def]
    org_a, org_b, _ = _seed_organizations(database)
    campaign_b = _insert_campaign(raw_engine, org_b, "Campaña de B")
    with pytest.raises(IntegrityError):
        run(
            _execute(
                raw_engine,
                ENTRY_SQL,
                {"id": uuid.uuid4(), "org": org_a, "campaign": campaign_b, "investment": 100},
            )
        )


def test_metric_entries_are_append_only(database, raw_engine):  # type: ignore[no-untyped-def]
    org_a, _, _ = _seed_organizations(database)
    campaign_a = _insert_campaign(raw_engine, org_a)
    entry_id = uuid.uuid4()
    run(_execute(raw_engine, ENTRY_SQL, {"id": entry_id, "org": org_a, "campaign": campaign_a, "investment": 100}))

    with pytest.raises(DBAPIError, match="append-only"):
        run(_execute(raw_engine, "UPDATE campaign_metric_entries SET source = 'text' WHERE id = :id", {"id": entry_id}))
    with pytest.raises(DBAPIError, match="append-only"):
        run(_execute(raw_engine, "DELETE FROM campaign_metric_entries WHERE id = :id", {"id": entry_id}))


def test_negative_values_and_invalid_source_are_rejected(database, raw_engine):  # type: ignore[no-untyped-def]
    org_a, _, _ = _seed_organizations(database)
    campaign_a = _insert_campaign(raw_engine, org_a)
    with pytest.raises(IntegrityError):
        run(_execute(raw_engine, ENTRY_SQL, {"id": uuid.uuid4(), "org": org_a, "campaign": campaign_a, "investment": -1}))
    with pytest.raises(IntegrityError):
        run(
            _execute(
                raw_engine,
                "INSERT INTO campaign_metric_entries (id, organization_id, campaign_id, source, raw_input)"
                " VALUES (:id, :org, :campaign, 'csv', 'x')",
                {"id": uuid.uuid4(), "org": org_a, "campaign": campaign_a},
            )
        )


def test_campaign_with_history_cannot_be_deleted(database, raw_engine):  # type: ignore[no-untyped-def]
    org_a, _, _ = _seed_organizations(database)
    campaign_a = _insert_campaign(raw_engine, org_a)
    run(_execute(raw_engine, ENTRY_SQL, {"id": uuid.uuid4(), "org": org_a, "campaign": campaign_a, "investment": 1}))
    with pytest.raises(IntegrityError):
        run(_execute(raw_engine, "DELETE FROM campaigns WHERE id = :id", {"id": campaign_a}))


def test_money_is_stored_as_exact_decimal_with_documented_precision(database, raw_engine):  # type: ignore[no-untyped-def]
    org_a, _, _ = _seed_organizations(database)
    campaign_a = _insert_campaign(raw_engine, org_a)
    entry_id = uuid.uuid4()
    run(
        _execute(
            raw_engine,
            "INSERT INTO campaign_metric_entries (id, organization_id, campaign_id, source, raw_input, investment, cpc, ctr)"
            " VALUES (:id, :org, :campaign, 'form', 'x', 1234567.005, 0.12345, 250.5)",
            {"id": entry_id, "org": org_a, "campaign": campaign_a},
        )
    )
    row = run(_row(raw_engine, entry_id))
    assert row == (Decimal("1234567.01"), Decimal("0.1235"), Decimal("250.5000"))


async def _row(raw_engine, entry_id):  # type: ignore[no-untyped-def]
    async with raw_engine.connect() as connection:
        result = await connection.execute(
            text("SELECT investment, cpc, ctr FROM campaign_metric_entries WHERE id = :id"), {"id": entry_id}
        )
        return tuple(result.one())


def test_campaign_tables_enable_row_level_security_without_policies(database, test_database_url):  # type: ignore[no-untyped-def]
    async def state():  # type: ignore[no-untyped-def]
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(test_database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                rls = await connection.execute(
                    text(
                        "SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace"
                        " AND relname IN ('campaigns', 'campaign_metric_entries') ORDER BY relname"
                    )
                )
                policies = await connection.scalar(
                    text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public'")
                )
                return [tuple(row) for row in rls], policies
        finally:
            await engine.dispose()

    rows, policies = run(state())
    assert rows == [("campaign_metric_entries", True), ("campaigns", True)]
    assert policies == 0
