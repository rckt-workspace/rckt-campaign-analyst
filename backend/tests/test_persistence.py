"""PostgreSQL integration: migrations, constraints, tenant scoping, rollback and persistence across connections."""

import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.repositories import MembershipRepository, OrganizationRepository, UserRepository
from app.domain.access import PAID_AI_ROLES, RECORD_CAMPAIGN_ROLES, Role
from app.services.access import OrganizationNotFoundError, PermissionDeniedError, require_role
from tests.conftest import alembic_config, run

EXPECTED_TABLES = {"organizations", "app_users", "organization_memberships", "alembic_version"}


async def _schema_diff(url: str) -> list:
    engine = create_async_engine(url, poolclass=NullPool)

    def compare(sync_connection):  # type: ignore[no-untyped-def]
        context = MigrationContext.configure(sync_connection, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)

    try:
        async with engine.connect() as connection:
            return await connection.run_sync(compare)
    finally:
        await engine.dispose()


async def _table_names(url: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return set(await connection.run_sync(lambda c: inspect(c).get_table_names()))
    finally:
        await engine.dispose()


def test_migrations_create_expected_tables(test_database_url):
    assert EXPECTED_TABLES <= run(_table_names(test_database_url))


def test_models_match_migrated_schema(test_database_url):
    # Fails when models.py changes without a new migration, or a migration changes the schema without models.py.
    assert run(_schema_diff(test_database_url)) == []


def test_downgrade_and_upgrade_round_trip(test_database_url):
    command.downgrade(alembic_config(), "base")
    try:
        assert "organizations" not in run(_table_names(test_database_url))
    finally:
        command.upgrade(alembic_config(), "head")
    assert run(_schema_diff(test_database_url)) == []


def test_organization_round_trip_and_listing(database):
    async def create():
        async with database.session() as session:
            organization = await OrganizationRepository(session).create(name="Cliente Uno", slug="cliente-uno")
            user = await UserRepository(session).create(external_subject="sub-analyst")
            await MembershipRepository(session).add(organization_id=organization.id, user_id=user.id, role=Role.ANALYST)
            return organization.id, user.id

    async def read(organization_id, user_id):
        async with database.session() as session:
            fetched = await OrganizationRepository(session).get(organization_id)
            listed = await OrganizationRepository(session).list_for_user(user_id)
            return fetched, [organization.slug for organization in listed]

    organization_id, user_id = run(create())
    fetched, slugs = run(read(organization_id, user_id))

    assert fetched is not None and fetched.name == "Cliente Uno"
    assert fetched.status == "active"
    assert fetched.created_at.tzinfo is not None  # timestamptz comes back timezone-aware
    assert slugs == ["cliente-uno"]


def test_duplicate_slug_is_rejected(database):
    async def create_twice():
        async with database.session() as session:
            await OrganizationRepository(session).create(name="A", slug="mismo")
        async with database.session() as session:
            await OrganizationRepository(session).create(name="B", slug="mismo")

    with pytest.raises(IntegrityError):
        run(create_twice())


def test_invalid_slug_and_status_are_rejected_by_database(raw_engine, database):
    async def insert(sql: str):
        async with raw_engine.begin() as connection:
            await connection.execute(text(sql))

    with pytest.raises(IntegrityError):
        run(insert("INSERT INTO organizations (id, name, slug) VALUES (gen_random_uuid(), 'x', 'Bad Slug')"))
    with pytest.raises(IntegrityError):
        run(insert("INSERT INTO organizations (id, name, slug, status) VALUES (gen_random_uuid(), 'x', 'ok-slug', 'deleted')"))


def test_membership_is_unique_per_organization_and_user(database):
    async def create_twice():
        async with database.session() as session:
            organization = await OrganizationRepository(session).create(name="A", slug="org-a")
            user = await UserRepository(session).create(external_subject="sub-1")
            await MembershipRepository(session).add(organization_id=organization.id, user_id=user.id, role=Role.OWNER)
        async with database.session() as session:
            await MembershipRepository(session).add(organization_id=organization.id, user_id=user.id, role=Role.ANALYST)

    with pytest.raises(IntegrityError):
        run(create_twice())


def test_unknown_role_is_rejected_by_database(raw_engine, database):
    async def create_membership_with_bad_role():
        async with database.session() as session:
            organization = await OrganizationRepository(session).create(name="A", slug="org-role")
            user = await UserRepository(session).create(external_subject="sub-role")
            ids = (organization.id, user.id)
        async with raw_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO organization_memberships (id, organization_id, user_id, role) "
                    "VALUES (gen_random_uuid(), :organization_id, :user_id, 'superadmin')"
                ),
                {"organization_id": ids[0], "user_id": ids[1]},
            )

    with pytest.raises(IntegrityError):
        run(create_membership_with_bad_role())


def test_memberships_scope_access_to_their_own_organizations(database):
    async def create():
        async with database.session() as session:
            org_a = await OrganizationRepository(session).create(name="Cliente A", slug="cliente-a")
            await OrganizationRepository(session).create(name="Cliente B", slug="cliente-b")
            analyst = await UserRepository(session).create(external_subject="sub-a-analyst")
            await MembershipRepository(session).add(organization_id=org_a.id, user_id=analyst.id, role=Role.ANALYST)
            return org_a.id, analyst.id

    async def check(org_a_id, user_id):
        async with database.session() as session:
            listed = await OrganizationRepository(session).list_for_user(user_id)
            org_b_id = next(o.id for o in await _all_organizations(session) if o.slug == "cliente-b")
            await require_role(session, organization_id=org_a_id, user_id=user_id, allowed=RECORD_CAMPAIGN_ROLES)
            with pytest.raises(OrganizationNotFoundError):
                await require_role(session, organization_id=org_b_id, user_id=user_id, allowed=RECORD_CAMPAIGN_ROLES)
            with pytest.raises(PermissionDeniedError):
                await require_role(session, organization_id=org_a_id, user_id=user_id, allowed={Role.OWNER})
            return [o.slug for o in listed]

    org_a_id, user_id = run(create())
    assert run(check(org_a_id, user_id)) == ["cliente-a"]


async def _all_organizations(session):  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.db.models import Organization

    return (await session.scalars(select(Organization))).all()


def test_client_viewer_cannot_run_paid_ai(database):
    async def create():
        async with database.session() as session:
            organization = await OrganizationRepository(session).create(name="Cliente C", slug="cliente-c")
            viewer = await UserRepository(session).create(external_subject="sub-viewer")
            await MembershipRepository(session).add(
                organization_id=organization.id, user_id=viewer.id, role=Role.CLIENT_VIEWER
            )
            return organization.id, viewer.id

    async def attempt(organization_id, viewer_id):
        async with database.session() as session:
            await require_role(session, organization_id=organization_id, user_id=viewer_id, allowed=PAID_AI_ROLES)

    organization_id, viewer_id = run(create())
    with pytest.raises(PermissionDeniedError):
        run(attempt(organization_id, viewer_id))


def test_data_persists_across_connections(database, test_database_url):
    async def write() -> uuid.UUID:
        async with database.session() as session:
            organization = await OrganizationRepository(session).create(name="Persistente", slug="persistente")
            return organization.id

    async def read_with_new_engine(organization_id):  # type: ignore[no-untyped-def]
        engine = create_async_engine(test_database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text("SELECT name FROM organizations WHERE id = :id"), {"id": organization_id}
                )
                return result.scalar_one()
        finally:
            await engine.dispose()

    assert run(read_with_new_engine(run(write()))) == "Persistente"


def test_failed_transaction_is_rolled_back(database):
    async def fail_after_insert():
        async with database.session() as session:
            await OrganizationRepository(session).create(name="Temporal", slug="temporal")
            raise RuntimeError("business rule failed after the insert")

    with pytest.raises(RuntimeError):
        run(fail_after_insert())

    async def count():
        async with database.session() as session:
            return len(await _all_organizations(session))

    assert run(count()) == 0


def test_check_constraint_names_match_the_models(test_database_url):
    # Regression: revision 0001 produced doubled and truncated CHECK names; 0002 renames them to the model names.
    async def names() -> set[str]:
        engine = create_async_engine(test_database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                rows = await connection.execute(
                    text("SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace AND contype = 'c'")
                )
                return {row[0] for row in rows}
        finally:
            await engine.dispose()

    assert run(names()) == {
        "ck_organizations_slug_format",
        "ck_organizations_status_valid",
        "ck_organization_memberships_role_valid",
        "ck_campaigns_name_not_blank",
        "ck_campaigns_currency_format",
        "ck_campaigns_period_order",
        "ck_campaign_metric_entries_source_valid",
        "ck_campaign_metric_entries_raw_input_length",
        "ck_campaign_metric_entries_period_order",
        "ck_campaign_metric_entries_previous_is_object",
        "ck_campaign_metric_entries_non_negative",
        "ck_analyses_status_valid",
        "ck_analyses_idempotency_key_not_blank",
        "ck_analyses_input_hash_format",
        "ck_analyses_prompt_version_not_blank",
        "ck_analyses_model_requested_not_blank",
        "ck_analyses_metric_entry_needs_campaign",
        "ck_analyses_error_code_valid",
        "ck_analyses_running_state",
        "ck_analyses_succeeded_state",
        "ck_analyses_failed_state",
        "ck_analyses_markdown_length",
        "ck_analyses_report_is_object",
        "ck_analyses_latency_non_negative",
        "ck_analyses_completed_after_created",
        "ck_analysis_usage_attempt_kind_valid",
        "ck_analysis_usage_status_valid",
        "ck_analysis_usage_model_not_blank",
        "ck_analysis_usage_error_code_valid",
        "ck_analysis_usage_state",
        "ck_analysis_usage_reported_values",
        "ck_analysis_usage_values_non_negative",
        "ck_analysis_usage_completed_after_created",
    }


def test_row_level_security_is_enabled_without_policies(test_database_url):
    # Roles exposed by managed APIs must see nothing until policies are written and verified.
    async def state() -> list[tuple[str, bool]]:
        engine = create_async_engine(test_database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                rows = await connection.execute(
                    text(
                        "SELECT relname, relrowsecurity FROM pg_class "
                        "WHERE relnamespace = 'public'::regnamespace "
                        "AND relname IN ('organizations', 'app_users', 'organization_memberships') ORDER BY relname"
                    )
                )
                policies = await connection.scalar(text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public'"))
                return [(row[0], row[1]) for row in rows] + [("policies", policies == 0)]
        finally:
            await engine.dispose()

    assert run(state()) == [
        ("app_users", True),
        ("organization_memberships", True),
        ("organizations", True),
        ("policies", True),
    ]
