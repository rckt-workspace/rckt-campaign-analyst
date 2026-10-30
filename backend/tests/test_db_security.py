"""Row level security and least privilege, exercised with the roles the application really uses.

Runs only when the disposable instance is configured (see scripts/security_poc/run_rls_suite.py):
  TEST_APP_DATABASE_URL       login of rckt_app: the FastAPI runtime. Business queries run here, through the application.
  TEST_OPERATOR_DATABASE_URL  login of a person who is a member of rckt_operator: the CLI.
Both must point to a database whose name ends in _test. The data is seeded by the owner-equivalent role (raw_engine, the
same as the rest of the suite), because seeding is not the behaviour under test. Every assertion about an application or
operator behaviour runs as that role.

Scope, stated plainly: these tests prove the database rules and the application paths they protect on this instance. They
do not prove the behaviour of Lovable Cloud or Supabase, whose default privileges and roles are not verified here.
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import cli
from app.core.config import DatabaseSettings
from app.db import analysis_ledger as ledger
from app.db.session import Database, bound_identity, identity_scope
from app.domain.access import Role
from app.domain.quota import QuotaExceededError
from app.main import app
from tests.api_fixtures import token_verifier  # noqa: F401  (pytest fixture)
from tests.auth_helpers import make_token
from tests.conftest import run
from tests.test_analysis_api import BODY, VALID, ok, post, provider  # noqa: F401  (pytest fixture)
from tests.test_analysis_ledger import PRIMARY, REPORT, rows
from tests.test_campaign_api import CAMPAIGN_BODY, add_member, seed
from tests.test_quota import set_quota

HASH = "e" * 64
LEASE = 120


# --- fixtures ----------------------------------------------------------------------------------------------------------

def _url(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not set: the row level security suite runs only on the disposable instance")
    if not value.startswith("postgresql+asyncpg://") or not value.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
        raise RuntimeError(f"{name} must be a postgresql+asyncpg:// URL to a database whose name ends in _test")
    return value


@pytest.fixture
def app_url(test_database_url):  # type: ignore[no-untyped-def]
    return _url("TEST_APP_DATABASE_URL")


@pytest.fixture
def operator_url(operator_database_url):  # type: ignore[no-untyped-def]
    return operator_database_url


@pytest.fixture
def app_database(app_url):  # type: ignore[no-untyped-def]
    database = Database(DatabaseSettings(url=app_url, pool_size=1, max_overflow=0, connect_timeout_seconds=5),
                        poolclass=NullPool)
    yield database
    run(database.dispose())


@pytest.fixture
def app_engine(app_url):  # type: ignore[no-untyped-def]
    engine = create_async_engine(app_url, poolclass=NullPool)
    yield engine
    run(engine.dispose())


@pytest.fixture
def operator_engine(operator_url):  # type: ignore[no-untyped-def]
    engine = create_async_engine(operator_url, poolclass=NullPool)
    yield engine
    run(engine.dispose())


@pytest.fixture
def app_client(app_database, token_verifier):  # type: ignore[no-untyped-def]
    """The real application, served with the application role. No lifespan and no network, as in api_fixtures."""
    from fastapi.testclient import TestClient

    app.state.database = app_database
    app.state.token_verifier = token_verifier
    try:
        yield TestClient(app)
    finally:
        app.state.database = None
        app.state.token_verifier = None


@pytest.fixture
def enforced(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "true")


# --- helpers -----------------------------------------------------------------------------------------------------------

async def _fetch(engine, statement: str, params: dict | None = None, *, subject: str | None = None) -> list[dict]:  # type: ignore[no-untyped-def]
    async with engine.begin() as connection:
        if subject is not None:
            await connection.execute(text("SELECT set_config('app.subject', :s, true)"), {"s": subject})
        return [dict(row._mapping) for row in (await connection.execute(text(statement), params or {})).all()]


def fetch_as(engine, statement: str, params: dict | None = None, *, subject: str | None = None) -> list[dict]:  # type: ignore[no-untyped-def]
    return run(_fetch(engine, statement, params, subject=subject))


def refused(engine, statement: str, params: dict | None = None, *, subject: str | None = None, reason: str) -> None:  # type: ignore[no-untyped-def]
    """The statement must be refused for the given reason. Any other outcome fails the test."""

    async def attempt():  # type: ignore[no-untyped-def]
        async with engine.begin() as connection:
            if subject is not None:
                await connection.execute(text("SELECT set_config('app.subject', :s, true)"), {"s": subject})
            await connection.execute(text(statement), params or {})

    with pytest.raises(DBAPIError) as info:
        run(attempt())
    message = f"{info.value} {info.value.orig}"
    assert reason in message, f"refused for another reason: {message[:300]}"


def org_ids(engine, subject: str) -> set[str]:  # type: ignore[no-untyped-def]
    return {str(row["id"]) for row in fetch_as(engine, "SELECT id FROM public.organizations", subject=subject)}


def raw_count(raw_engine, sql: str, params: dict | None = None) -> int:  # type: ignore[no-untyped-def]
    return rows(raw_engine, sql, params)[0]["n"]


def token_for(subject: str) -> dict:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {make_token(subject)}"}


def campaigns_path(organization_id) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/organizations/{organization_id}/campaigns"


# =========================================================================================================================
# A. Roles and privileges: who owns what, and what the application and the operator can never do.
# =========================================================================================================================

def test_application_and_operator_roles_are_not_superusers_and_do_not_bypass_rls(raw_engine):  # type: ignore[no-untyped-def]
    found = rows(raw_engine, "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles"
                             " WHERE rolname IN ('rckt_app', 'rckt_operator_test') ORDER BY rolname")
    assert [(r["rolname"], r["rolsuper"], r["rolbypassrls"]) for r in found] == [
        ("rckt_app", False, False),
        ("rckt_operator_test", False, False),
    ]


def test_application_and_operator_own_no_table(raw_engine):  # type: ignore[no-untyped-def]
    owned = raw_count(raw_engine,
                      "SELECT count(*) AS n FROM pg_tables WHERE schemaname = 'public'"
                      " AND tableowner IN ('rckt_app', 'rckt_operator_test')")
    assert owned == 0


def test_application_cannot_truncate_alter_disable_triggers_or_change_policies(app_engine):  # type: ignore[no-untyped-def]
    refused(app_engine, "TRUNCATE public.audit_events", reason="permission denied")
    refused(app_engine, "TRUNCATE public.campaigns", reason="permission denied")
    refused(app_engine, "ALTER TABLE public.campaigns ADD COLUMN poc_x int", reason="must be owner")
    refused(app_engine, "ALTER TABLE public.audit_events DISABLE TRIGGER trg_audit_events_append_only",
            reason="must be owner")
    refused(app_engine, "CREATE POLICY poc_open ON public.campaigns FOR SELECT USING (true)", reason="must be owner")
    refused(app_engine, "CREATE TABLE public.poc_new (id int)", reason="permission denied")
    refused(app_engine, "SET ROLE rckt_schema_owner", reason="permission denied")


def test_a_table_created_later_grants_nothing_and_a_function_once_revoked_is_refused(app_engine, raw_engine):  # type: ignore[no-untyped-def]
    # Future objects, created by the schema owner as a migration would. Tables grant nothing by default. Functions grant
    # EXECUTE to PUBLIC by default, so the migration that creates one must revoke it: this test performs that revoke.
    raw_run(raw_engine, "CREATE TABLE public.zz_future_table (id int)")
    raw_run(raw_engine, "CREATE FUNCTION public.zz_future_function() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$")
    raw_run(raw_engine, "REVOKE EXECUTE ON FUNCTION public.zz_future_function() FROM PUBLIC")
    try:
        refused(app_engine, "SELECT count(*) FROM public.zz_future_table", reason="permission denied")
        refused(app_engine, "SELECT public.zz_future_function()", reason="permission denied")
    finally:
        raw_run(raw_engine, "DROP FUNCTION public.zz_future_function()")
        raw_run(raw_engine, "DROP TABLE public.zz_future_table")


def test_no_function_in_public_is_executable_by_public(raw_engine):  # type: ignore[no-untyped-def]
    # Regression guard for the rule above: a migration that creates a function and forgets to revoke PUBLIC fails here.
    exposed = rows(raw_engine,
                   "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                   " WHERE n.nspname = 'public' AND has_function_privilege('public', p.oid, 'EXECUTE') ORDER BY 1")
    assert exposed == [], f"functions executable by PUBLIC: {[row['proname'] for row in exposed]}"


def test_application_cannot_update_or_delete_audit_events(app_engine, raw_engine):  # type: ignore[no-untyped-def]
    # One event to attack: written by the owner-equivalent role, so the attempt has something to touch.
    raw_run(raw_engine, "INSERT INTO public.audit_events (organization_id, source, action, resource_type, resource_id)"
                        " SELECT id, 'internal', 'quota.updated', 'analysis_quota', id::text FROM public.organizations"
                        " LIMIT 1")
    refused(app_engine, "UPDATE public.audit_events SET action = action", reason="permission denied")
    refused(app_engine, "DELETE FROM public.audit_events", reason="permission denied")


def test_no_privilege_is_granted_to_public_anon_or_authenticated(raw_engine):  # type: ignore[no-untyped-def]
    granted = raw_count(raw_engine,
                        "SELECT count(*) AS n FROM information_schema.role_table_grants"
                        " WHERE table_schema = 'public' AND grantee IN ('PUBLIC', 'anon', 'authenticated')")
    assert granted == 0


def test_operator_reads_and_writes_no_table_directly(operator_engine):  # type: ignore[no-untyped-def]
    refused(operator_engine, "SELECT count(*) FROM public.app_users", reason="permission denied")
    refused(operator_engine, "SELECT count(*) FROM public.audit_events", reason="permission denied")
    refused(operator_engine, "INSERT INTO public.organizations (name, slug) VALUES ('x', 'direct-write')",
            reason="permission denied")


def test_operator_functions_are_not_executable_by_the_application_or_public(raw_engine):  # type: ignore[no-untyped-def]
    for signature in ("operator_create_organization(text, text)", "operator_provision_user(text, text)",
                      "operator_grant_membership(text, text, text, text)",
                      "operator_set_analysis_quota(text, text, integer)"):
        app_can = raw_count(raw_engine, "SELECT CASE WHEN has_function_privilege('rckt_app', :f, 'EXECUTE')"
                                        " THEN 1 ELSE 0 END AS n", {"f": f"public.{signature}"})
        public_can = raw_count(raw_engine, "SELECT CASE WHEN has_function_privilege('public', :f, 'EXECUTE')"
                                           " THEN 1 ELSE 0 END AS n", {"f": f"public.{signature}"})
        assert app_can == 0 and public_can == 0, signature


def test_operator_functions_are_definer_owned_by_the_schema_owner_with_a_fixed_search_path(raw_engine):  # type: ignore[no-untyped-def]
    found = rows(raw_engine, "SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner) AS owner, p.proconfig"
                             " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                             " WHERE n.nspname = 'public' AND p.proname LIKE 'operator\\_%' ORDER BY 1")
    assert len(found) == 4
    for row in found:
        assert row["prosecdef"] is True, row["proname"]
        assert row["owner"] == "rckt_schema_owner", row["proname"]
        assert "search_path=pg_catalog, public" in row["proconfig"], row["proname"]


def raw_run(raw_engine, statement: str) -> None:  # type: ignore[no-untyped-def]
    async def execute():  # type: ignore[no-untyped-def]
        async with raw_engine.begin() as connection:
            await connection.execute(text(statement))

    run(execute())


# =========================================================================================================================
# B. Identity context: what a session sees with and without identity, and what it cannot leak.
# =========================================================================================================================

def test_without_identity_no_business_row_is_visible(app_engine, database):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=Role.OWNER, subject="ctx-owner", org_slug="ctx-a")
    for statement in ("SELECT id FROM public.organizations", "SELECT id FROM public.campaigns",
                      "SELECT id FROM public.analyses", "SELECT id FROM public.audit_events",
                      "SELECT id FROM public.organization_memberships", "SELECT id FROM public.app_users"):
        assert fetch_as(app_engine, statement) == [], statement


def test_identity_is_bound_to_one_task_and_never_crosses_concurrent_requests(database, app_database, raw_engine):  # type: ignore[no-untyped-def]
    org_a, _ = seed(database, role=Role.OWNER, subject="iso-a", org_slug="iso-a")
    org_b, _ = seed(database, role=Role.OWNER, subject="iso-b", org_slug="iso-b")

    async def read(subject: str) -> list[str]:
        with identity_scope(subject):
            await asyncio.sleep(0)  # let the other request run in between
            async with app_database.session() as session:
                result = await session.execute(text("SELECT id FROM public.organizations"))
                await asyncio.sleep(0)
                return sorted(str(row[0]) for row in result.all())

    async def both():  # type: ignore[no-untyped-def]
        return await asyncio.gather(read("iso-a"), read("iso-b"))

    seen_a, seen_b = run(both())
    assert seen_a == [str(org_a)]
    assert seen_b == [str(org_b)]
    assert bound_identity() is None, "the identity must not outlive its scope"


def test_pooled_connection_keeps_no_identity_after_a_session(database, app_url, raw_engine):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="pool-owner", org_slug="pool-a")

    async def sequence():  # type: ignore[no-untyped-def]
        # The application role: the one that serves requests. The migrator is not used here (see the note in the report).
        pooled = Database(DatabaseSettings(url=app_url, pool_size=1, max_overflow=0, connect_timeout_seconds=5))
        try:
            # The same pooled connection serves both sessions (pool_size=1, one event loop).
            with identity_scope("pool-owner"):
                async with pooled.session() as session:
                    seen_with = (await session.execute(text("SELECT count(*) FROM public.organizations"))).scalar_one()
            async with pooled.session() as session:
                seen_after = (await session.execute(text("SELECT count(*) FROM public.organizations"))).scalar_one()
                setting = (await session.execute(text("SELECT current_setting('app.subject', true)"))).scalar_one()
            return seen_with, seen_after, setting
        finally:
            await pooled.dispose()

    with_identity, after, setting = run(sequence())
    assert with_identity == 1 and after == 0
    assert not setting, "no session-level setting may survive a transaction"


def test_snapshot_session_is_repeatable_read_read_only_and_identity_bound(database, app_database, raw_engine):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="snap-owner", org_slug="snap-a")

    async def scenario():  # type: ignore[no-untyped-def]
        with identity_scope("snap-owner"):
            async with app_database.session(snapshot=True) as session:
                level = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
                read_only = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
                bound = (await session.execute(text("SELECT current_setting('app.subject', true)"))).scalar_one()
                first = (await session.execute(text("SELECT count(*) FROM public.organizations"))).scalar_one()
                # The owner-equivalent role writes a new organization while this snapshot is open.
                await _owner_add_membership(raw_engine, "snap-owner", "snap-b")
                second = (await session.execute(text("SELECT count(*) FROM public.organizations"))).scalar_one()
                try:
                    await session.execute(text("INSERT INTO public.app_users (external_subject) VALUES ('snap-write')"))
                    write_refused = False
                except DBAPIError:
                    write_refused = True
                return level, read_only, bound, first, second, write_refused

    level, read_only, bound, first, second, write_refused = run(scenario())
    assert level == "repeatable read"
    assert read_only == "on"
    assert bound == "snap-owner"
    assert first == second == 1, "a repeatable-read snapshot must not see the concurrent membership"
    assert write_refused
    # Proof that the write happened and is visible to a new transaction, so the equality above is not vacuous.
    assert raw_count(raw_engine, "SELECT count(*) AS n FROM public.organization_memberships m"
                                 " JOIN public.app_users u ON u.id = m.user_id WHERE u.external_subject = 'snap-owner'") == 2


async def _owner_add_membership(raw_engine, subject: str, slug: str) -> None:  # type: ignore[no-untyped-def]
    async with raw_engine.begin() as connection:
        await connection.execute(text(
            "INSERT INTO public.organizations (name, slug) VALUES (:s, :s)"), {"s": slug})
        await connection.execute(text(
            "INSERT INTO public.organization_memberships (organization_id, user_id, role)"
            " SELECT o.id, u.id, 'owner' FROM public.organizations o, public.app_users u"
            " WHERE o.slug = :s AND u.external_subject = :u"), {"s": slug, "u": subject})


def test_binding_refuses_an_empty_subject():
    from app.db.session import bind_identity

    with pytest.raises(ValueError):
        bind_identity("   ")


# =========================================================================================================================
# C. Isolation through the application: the same HTTP paths a user takes, served with rckt_app.
# =========================================================================================================================

def test_me_lists_every_organization_of_a_user_with_several_memberships(database, app_client, raw_engine):  # type: ignore[no-untyped-def]
    org_a, user = seed(database, role=Role.CLIENT_VIEWER, subject="multi-user", org_slug="multi-a")
    org_b, _ = seed(database, role=None, subject="multi-owner-b", org_slug="multi-b")
    add_member(database, organization_id=org_b, subject="multi-user-b", role=Role.ANALYST)
    # The same person in both organizations, through the owner-equivalent seed path.
    run(_second_membership(raw_engine, user, org_b, Role.ANALYST))

    response = app_client.get("/api/v1/me", headers=token_for("multi-user"))

    assert response.status_code == 200
    roles = {item["slug"]: item["role"] for item in response.json()["organizations"]}
    assert roles == {"multi-a": "client_viewer", "multi-b": "analyst"}


async def _second_membership(raw_engine, user, organization_id, role) -> None:  # type: ignore[no-untyped-def]
    async with raw_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO public.organization_memberships (organization_id, user_id, role)"
                 " VALUES (:o, :u, :r)"),
            {"o": organization_id, "u": user, "r": role.value},
        )


def test_an_owner_cannot_read_or_touch_campaigns_of_another_organization(database, app_client, raw_engine):  # type: ignore[no-untyped-def]
    org_a, _ = seed(database, role=Role.OWNER, subject="cross-owner-a", org_slug="cross-a")
    org_b, _ = seed(database, role=Role.OWNER, subject="cross-owner-b", org_slug="cross-b")
    created = app_client.post(campaigns_path(org_b), json=CAMPAIGN_BODY, headers=token_for("cross-owner-b"))
    assert created.status_code == 201
    campaign_id = created.json()["id"]

    assert app_client.get(campaigns_path(org_b), headers=token_for("cross-owner-a")).status_code == 404
    assert app_client.get(f"{campaigns_path(org_a)}/{campaign_id}", headers=token_for("cross-owner-a")).status_code == 404
    assert app_client.post(campaigns_path(org_b), json=CAMPAIGN_BODY, headers=token_for("cross-owner-a")).status_code == 404


def test_owner_and_analyst_create_campaigns_and_the_client_viewer_cannot(database, app_client, raw_engine):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="roles-owner", org_slug="roles-a")
    add_member(database, organization_id=org, subject="roles-analyst", role=Role.ANALYST)
    add_member(database, organization_id=org, subject="roles-viewer", role=Role.CLIENT_VIEWER)

    assert app_client.post(campaigns_path(org), json=CAMPAIGN_BODY, headers=token_for("roles-owner")).status_code == 201
    assert app_client.post(campaigns_path(org), json=CAMPAIGN_BODY, headers=token_for("roles-analyst")).status_code == 201
    assert app_client.post(campaigns_path(org), json=CAMPAIGN_BODY, headers=token_for("roles-viewer")).status_code == 403
    assert app_client.get(campaigns_path(org), headers=token_for("roles-viewer")).status_code == 200


def test_a_viewer_never_starts_an_analysis_and_the_provider_is_never_called(database, app_client, raw_engine, provider):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="deny-owner", org_slug="deny-a")
    add_member(database, organization_id=org, subject="deny-viewer", role=Role.CLIENT_VIEWER)

    response = post(app_client, org, "deny-viewer", "key-deny-viewer-01")

    assert response.status_code == 403
    assert provider.calls == 0
    assert raw_count(raw_engine, "SELECT count(*) AS n FROM public.analyses WHERE organization_id = :o", {"o": org}) == 0


def test_an_analyst_analysis_is_persisted_replayed_without_a_unit_or_a_call(database, app_client, raw_engine, provider, enforced):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="life-owner", org_slug="life-a")
    add_member(database, organization_id=org, subject="life-analyst", role=Role.ANALYST)
    set_quota(database, org, limit=5)
    provider.replies = [ok(VALID)]

    first = post(app_client, org, "life-analyst", "key-life-analysis-1")
    replay = post(app_client, org, "life-analyst", "key-life-analysis-1")

    assert first.status_code == 201 and first.json()["status"] == "succeeded"
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    assert provider.calls == 1, "a replay must not reach the provider"
    consumed = raw_count(raw_engine, "SELECT consumed AS n FROM public.analysis_quota_usage WHERE organization_id = :o"
                                     " ORDER BY period_start DESC LIMIT 1", {"o": org})
    assert consumed == 1, "a replay must not consume a second unit"


def test_attempts_reach_their_final_state_through_the_application(database, app_client, raw_engine, provider):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="att-owner", org_slug="att-a")
    provider.replies = [ok(VALID)]

    created = post(app_client, org, "att-owner", "key-att-analysis-1")

    attempts = rows(raw_engine, "SELECT status, step FROM public.analysis_usage WHERE analysis_id = :a",
                    {"a": uuid.UUID(created.json()["id"])})
    assert [(a["status"], a["step"]) for a in attempts] == [("succeeded", "initial")]


def test_quota_exhausted_answers_429_and_the_provider_is_not_called(database, app_client, raw_engine, provider, enforced):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="quo-owner", org_slug="quo-a")
    set_quota(database, org, limit=1)
    provider.replies = [ok(VALID)]
    assert post(app_client, org, "quo-owner", "key-quo-first-001").status_code == 201

    second = post(app_client, org, "quo-owner", "key-quo-second-01", {"data": {**BODY["data"], "investment": 2500}})

    assert second.status_code == 429
    assert provider.calls == 1, "the refused request must not call the provider"


def test_the_audit_trail_is_owner_only_and_records_the_analysis(database, app_client, raw_engine, provider):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="aud-owner", org_slug="aud-a")
    add_member(database, organization_id=org, subject="aud-analyst", role=Role.ANALYST)
    provider.replies = [ok(VALID)]
    assert post(app_client, org, "aud-analyst", "key-aud-analysis-1").status_code == 201

    owner_view = app_client.get(f"/api/v1/organizations/{org}/audit-events", headers=token_for("aud-owner"))
    analyst_view = app_client.get(f"/api/v1/organizations/{org}/audit-events", headers=token_for("aud-analyst"))

    assert analyst_view.status_code == 403
    actions = {event["action"] for event in owner_view.json()["items"]}
    assert {"analysis.created", "analysis.succeeded"} <= actions


def test_the_usage_report_is_owner_only_and_each_read_is_audited(database, app_client, raw_engine):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="rep-owner", org_slug="rep-a")
    add_member(database, organization_id=org, subject="rep-analyst", role=Role.ANALYST)

    denied = app_client.get(f"/api/v1/organizations/{org}/usage", headers=token_for("rep-analyst"))
    allowed = app_client.get(f"/api/v1/organizations/{org}/usage", headers=token_for("rep-owner"))

    assert denied.status_code == 403
    assert allowed.status_code == 200
    accessed = raw_count(raw_engine, "SELECT count(*) AS n FROM public.audit_events"
                                     " WHERE organization_id = :o AND action = 'usage.report_accessed'"
                                     " AND actor_user_id IS NOT NULL", {"o": org})
    assert accessed == 1


# =========================================================================================================================
# D. Lifecycle, quota and audit rules at the ledger level, as rckt_app.
# =========================================================================================================================

def _claim(app_database, org, user, key: str, *, enforce: bool = True):  # type: ignore[no-untyped-def]
    return ledger.claim_analysis(
        app_database, organization_id=org, requested_by=user, campaign_id=None, metric_entry_id=None,
        idempotency_key=key, input_hash=HASH, prompt_version="analysis-v1", model_requested=PRIMARY,
        deterministic_report=REPORT, lease_seconds=LEASE, quota=ledger.QuotaClaim(enforce=enforce),
    )


def test_an_abandoned_lease_makes_its_attempts_unknown_and_records_the_failure(database, app_database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=Role.ANALYST, subject="aband-analyst", org_slug="aband-a")
    set_quota(database, org, limit=3)

    async def scenario():  # type: ignore[no-untyped-def]
        with identity_scope("aband-analyst"):
            claim = await _claim(app_database, org, user, "key-aband-0000001")
            attempt = await ledger.reserve_attempt(app_database, organization_id=org, analysis_id=claim.analysis_id,
                                                   attempt_number=1, step="initial", model_role="primary", model=PRIMARY)
        return claim, attempt

    claim, _ = run(scenario())
    # The lease expires (the owner-equivalent role moves it into the past; the guard still allows it while running).
    raw_run(raw_engine, f"UPDATE public.analyses SET lease_expires_at = now() - interval '1 minute'"
                        f" WHERE id = '{claim.analysis_id}'")

    async def abandon():  # type: ignore[no-untyped-def]
        with identity_scope("aband-analyst"):
            return await ledger.abandon_expired(app_database, organization_id=org, analysis_id=claim.analysis_id)

    assert run(abandon()) is True
    attempts = rows(raw_engine, "SELECT status, error_code FROM public.analysis_usage WHERE analysis_id = :a",
                    {"a": claim.analysis_id})
    assert [(a["status"], a["error_code"]) for a in attempts] == [("unknown", "ANALYSIS_ABANDONED")]
    failed = rows(raw_engine, "SELECT source, actor_user_id FROM public.audit_events"
                              " WHERE action = 'analysis.failed' AND resource_id = :r", {"r": str(claim.analysis_id)})
    assert [(f["source"], f["actor_user_id"]) for f in failed] == [("internal", None)]


def test_concurrent_claims_with_one_unit_left_admit_exactly_one(database, app_database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=Role.ANALYST, subject="race-analyst", org_slug="race-a")
    set_quota(database, org, limit=1)

    async def race():  # type: ignore[no-untyped-def]
        async def one(key: str):  # type: ignore[no-untyped-def]
            with identity_scope("race-analyst"):
                return await _claim(app_database, org, user, key)

        return await asyncio.gather(one("key-race-app-0001"), one("key-race-app-0002"), return_exceptions=True)

    results = run(race())
    assert sum(1 for r in results if getattr(r, "outcome", None) == "claimed") == 1
    assert sum(1 for r in results if isinstance(r, QuotaExceededError)) == 1


def test_a_refused_audit_event_rolls_back_the_whole_transaction(database, app_database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=Role.OWNER, subject="roll-owner", org_slug="roll-a")

    async def write():  # type: ignore[no-untyped-def]
        with identity_scope("roll-owner"):
            async with app_database.session() as session:
                await session.execute(text("INSERT INTO public.campaigns (organization_id, name, created_by)"
                                           " VALUES (:o, 'roll back me', :u)"), {"o": org, "u": user})
                # An event that names another actor is refused by the policy: the campaign must not survive it.
                await session.execute(text(
                    "INSERT INTO public.audit_events (organization_id, source, actor_user_id, action, resource_type,"
                    " resource_id) VALUES (:o, 'user', :other, 'analysis.created', 'analysis', 'x')"),
                    {"o": org, "other": uuid.uuid4()})

    with pytest.raises(DBAPIError):
        run(write())
    assert raw_count(raw_engine, "SELECT count(*) AS n FROM public.campaigns WHERE name = 'roll back me'") == 0


def test_audit_events_from_the_application_are_attributed_and_scoped(database, app_engine, raw_engine):  # type: ignore[no-untyped-def]
    org_a, user_a = seed(database, role=Role.OWNER, subject="scope-a", org_slug="scope-a")
    org_b, _ = seed(database, role=Role.OWNER, subject="scope-b", org_slug="scope-b")
    other = uuid.uuid4()

    refused(app_engine, "INSERT INTO public.audit_events (organization_id, source, actor_user_id, action,"
                        " resource_type, resource_id) VALUES (:o, 'user', :other, 'usage.report_accessed',"
                        " 'usage_report', 'x')", {"o": org_a, "other": other},
            subject="scope-a", reason="row-level security")
    refused(app_engine, "INSERT INTO public.audit_events (organization_id, source, action, resource_type, resource_id)"
                        " VALUES (:o, 'internal', 'analysis.succeeded', 'analysis', 'not-an-analysis')", {"o": org_a},
            subject="scope-a", reason="row-level security")
    refused(app_engine, "INSERT INTO public.audit_events (organization_id, source, action, resource_type, resource_id)"
                        " VALUES (:o, 'operator_cli', 'quota.updated', 'analysis_quota', 'x')", {"o": org_a},
            subject="scope-a", reason="row-level security")
    refused(app_engine, "INSERT INTO public.audit_events (organization_id, source, action, resource_type, resource_id)"
                        " VALUES (:o, 'user', 'usage.report_accessed', 'usage_report', 'x')", {"o": org_b},
            subject="scope-a", reason="row-level security")
    # The legitimate user event: attributed to the caller, for a report read by an owner of that organization.
    run(_insert_user_event(app_engine, org_a, user_a))


async def _insert_user_event(app_engine, org, user) -> None:  # type: ignore[no-untyped-def]
    async with app_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.subject', 'scope-a', true)"))
        await connection.execute(text(
            "INSERT INTO public.audit_events (organization_id, source, actor_user_id, action, resource_type, resource_id)"
            " VALUES (:o, 'user', :u, 'usage.report_accessed', 'usage_report', 'accepted')"), {"o": org, "u": user})


# =========================================================================================================================
# E. Operator: the four CLI commands run as the operator login, traceable and bounded to their functions.
# =========================================================================================================================

def test_the_cli_completes_its_four_operations_as_the_operator_login(monkeypatch, operator_url, raw_engine):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_OPERATOR_URL", operator_url)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)

    assert cli.main(["create-organization", "--name", "Op Cliente", "--slug", "op-cliente"]) == 0
    assert cli.main(["provision-user", "--subject", "op-sub", "--email", "op@cliente.test"]) == 0
    assert cli.main(["grant-membership", "--organization-slug", "op-cliente", "--role", "analyst",
                     "--subject", "op-sub"]) == 0
    assert cli.main(["set-analysis-quota", "--organization-slug", "op-cliente", "--plan", "pilot",
                     "--monthly-limit", "7"]) == 0
    assert cli.main(["set-analysis-quota", "--organization-slug", "op-cliente", "--plan", "pilot",
                     "--monthly-limit", "7"]) == 0
    assert raw_count(raw_engine, "SELECT count(*) AS n FROM public.organization_memberships m"
                                 " JOIN public.organizations o ON o.id = m.organization_id WHERE o.slug = 'op-cliente'") == 1
    assert raw_count(raw_engine, "SELECT count(*) AS n FROM public.analysis_quotas q"
                                 " JOIN public.organizations o ON o.id = q.organization_id WHERE o.slug = 'op-cliente'") == 1


def test_operator_events_carry_the_database_login_and_no_invented_actor(monkeypatch, operator_url, raw_engine):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_OPERATOR_URL", operator_url)
    assert cli.main(["create-organization", "--name", "Attr Cliente", "--slug", "attr-cliente"]) == 0
    assert cli.main(["provision-user", "--subject", "attr-sub"]) == 0
    assert cli.main(["grant-membership", "--organization-slug", "attr-cliente", "--role", "owner",
                     "--subject", "attr-sub"]) == 0
    assert cli.main(["set-analysis-quota", "--organization-slug", "attr-cliente", "--plan", "standard",
                     "--monthly-limit", "9"]) == 0

    events = rows(raw_engine, "SELECT action, source, actor_user_id, db_principal FROM public.audit_events"
                              " WHERE organization_id = (SELECT id FROM public.organizations WHERE slug = 'attr-cliente')"
                              " ORDER BY id")
    assert [(e["action"], e["source"], e["actor_user_id"], e["db_principal"]) for e in events] == [
        ("membership.granted", "operator_cli", None, "rckt_operator_test"),
        ("quota.updated", "operator_cli", None, "rckt_operator_test"),
    ]


def test_the_operator_functions_refuse_invalid_input_without_writing(operator_engine, raw_engine):  # type: ignore[no-untyped-def]
    refused(operator_engine, "SELECT public.operator_create_organization(:n, :s)",
            {"n": "x", "s": "robert'); DROP TABLE organizations; --"}, reason="INVALID_SLUG")
    refused(operator_engine, "SELECT public.operator_create_organization('x', 'UPPER')", reason="INVALID_SLUG")
    refused(operator_engine, "SELECT public.operator_grant_membership('nope', 'sub', NULL, 'root')", reason="INVALID_ROLE")
    refused(operator_engine, "SELECT public.operator_set_analysis_quota('nope', 'gold', 5)", reason="INVALID_PLAN")
    refused(operator_engine, "SELECT public.operator_set_analysis_quota('nope', 'pilot', -1)", reason="INVALID_LIMIT")
    refused(operator_engine, "SELECT public.operator_grant_membership('nope', NULL, NULL, 'owner')",
            reason="SUBJECT_OR_EMAIL_REQUIRED")
    refused(operator_engine, "SELECT public.operator_grant_membership('no-such-org', 'sub', NULL, 'owner')",
            reason="ORGANIZATION_NOT_FOUND")
    assert raw_count(raw_engine, "SELECT count(*) AS n FROM public.organizations WHERE slug LIKE 'Robert%'") == 0


def test_the_operator_refuses_a_second_membership_for_the_same_person(database, monkeypatch, operator_url, raw_engine):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_OPERATOR_URL", operator_url)
    assert cli.main(["create-organization", "--name", "Dup Cliente", "--slug", "dup-cliente"]) == 0
    assert cli.main(["provision-user", "--subject", "dup-sub"]) == 0
    assert cli.main(["grant-membership", "--organization-slug", "dup-cliente", "--role", "analyst",
                     "--subject", "dup-sub"]) == 0
    with pytest.raises(SystemExit, match="membership already exists"):
        cli.main(["grant-membership", "--organization-slug", "dup-cliente", "--role", "owner", "--subject", "dup-sub"])
    assert raw_count(raw_engine, "SELECT count(*) AS n FROM public.organization_memberships m"
                                 " JOIN public.organizations o ON o.id = m.organization_id WHERE o.slug = 'dup-cliente'") == 1


def test_a_database_error_inside_an_operator_function_changes_nothing(monkeypatch, operator_url, raw_engine):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_OPERATOR_URL", operator_url)
    assert cli.main(["create-organization", "--name", "Once Cliente", "--slug", "once-cliente"]) == 0
    with pytest.raises(SystemExit, match="that slug is already in use"):
        cli.main(["create-organization", "--name", "Twice", "--slug", "once-cliente"])
    assert raw_count(raw_engine, "SELECT count(*) AS n FROM public.organizations WHERE slug = 'once-cliente'") == 1


def test_the_cli_never_reads_the_application_or_migration_urls(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://rckt_app@127.0.0.1:1/rckt_test")
    monkeypatch.setenv("DATABASE_MIGRATION_URL", "postgresql+asyncpg://rckt_migrator@127.0.0.1:1/rckt_test")
    monkeypatch.delenv("DATABASE_OPERATOR_URL", raising=False)
    with pytest.raises(SystemExit, match="DATABASE_OPERATOR_URL"):
        cli._database()


def test_the_operator_cannot_list_business_data_through_a_route(operator_engine, raw_engine):  # type: ignore[no-untyped-def]
    # The operator has no table privilege and no policy. The only path to organizations is a function with an exact key.
    refused(operator_engine, "SELECT id, name FROM public.organizations", reason="permission denied")
    refused(operator_engine, "SELECT * FROM public.app_users", reason="permission denied")
