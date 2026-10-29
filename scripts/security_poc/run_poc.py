"""Proof of concept: least-privilege roles and identity-scoped RLS, on a disposable PostgreSQL instance only.

Safety rules, enforced in code:
- Refuses any host other than 127.0.0.1. Refuses a database name that does not end in _poc_test.
- Creates cluster-wide roles, so it must run on an instance created for this proof of concept and nothing else.
  Start one with:
      docker run -d --name rckt-sec-poc-pg -p 127.0.0.1:PORT:5432 -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine
  and remove it with `docker rm -f rckt-sec-poc-pg` when finished.
- Uses no credentials: the instance accepts local connections without a password (trust, disposable instance).
- Never touches the migrations in supabase/migrations. It applies them as the owner role, read from disk.

Usage (venv Python, from the repository root):
    POC_PORT=56412 python scripts/security_poc/run_poc.py
"""

import asyncio
import glob
import os
import sys
import uuid
from pathlib import Path

import asyncpg

REPO = Path(__file__).resolve().parents[2]
POLICIES = Path(__file__).resolve().parent / "poc_policies.sql"
DB = "rckt_poc_test"
HOST = "127.0.0.1"
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def port() -> int:
    raw = os.environ.get("POC_PORT", "").strip()
    if not raw.isdigit():
        raise SystemExit("Set POC_PORT to the port of the disposable PostgreSQL instance.")
    return int(raw)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, PASS if ok else FAIL, detail))


async def connect(user: str, database: str = DB) -> asyncpg.Connection:
    return await asyncpg.connect(host=HOST, port=port(), user=user, database=database, timeout=10)


async def setup_roles() -> None:
    admin = await connect("postgres", "postgres")
    try:
        await admin.execute(
            """
            DO $$
            DECLARE r text;
            BEGIN
                FOREACH r IN ARRAY ARRAY['rckt_owner', 'rckt_app', 'rckt_operator'] LOOP
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                        EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB', r);
                    END IF;
                END LOOP;
                FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                        EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOBYPASSRLS', r);
                    END IF;
                END LOOP;
            END $$;
            """
        )
        await admin.execute(f"DROP DATABASE IF EXISTS {DB}")
        await admin.execute(f"CREATE DATABASE {DB} OWNER rckt_owner")
    finally:
        await admin.close()


async def apply_migrations() -> None:
    owner = await connect("rckt_owner")
    try:
        for path in sorted(glob.glob(str(REPO / "supabase" / "migrations" / "*.sql"))):
            await owner.execute(Path(path).read_text(encoding="utf-8"))
        await owner.execute(POLICIES.read_text(encoding="utf-8"))
    finally:
        await owner.close()


async def seed() -> dict[str, uuid.UUID]:
    owner = await connect("rckt_owner")
    ids = {name: uuid.uuid4() for name in (
        "org_a", "org_b", "owner_a", "analyst_a", "viewer_a", "owner_b", "multi", "campaign_a", "campaign_b",
        "analysis_a", "event_a", "event_b",
    )}
    subjects = {"owner_a": "sub-owner-a", "analyst_a": "sub-analyst-a", "viewer_a": "sub-viewer-a",
                "owner_b": "sub-owner-b", "multi": "sub-multi"}
    try:
        await owner.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, 'Cliente A', 'cliente-a'),"
                            " ($2, 'Cliente B', 'cliente-b')", ids["org_a"], ids["org_b"])
        for key, subject in subjects.items():
            await owner.execute("INSERT INTO app_users (id, external_subject, email) VALUES ($1, $2, $3)",
                                ids[key], subject, f"{key}@poc.invalid")
        memberships = [
            ("owner_a", "org_a", "owner"), ("analyst_a", "org_a", "analyst"), ("viewer_a", "org_a", "client_viewer"),
            ("owner_b", "org_b", "owner"), ("multi", "org_a", "client_viewer"), ("multi", "org_b", "analyst"),
        ]
        for user, org, role in memberships:
            await owner.execute("INSERT INTO organization_memberships (organization_id, user_id, role) VALUES ($1, $2, $3)",
                                ids[org], ids[user], role)
        await owner.execute("INSERT INTO campaigns (id, organization_id, name, created_by) VALUES ($1, $2, 'Campaña A', $3)",
                            ids["campaign_a"], ids["org_a"], ids["owner_a"])
        await owner.execute("INSERT INTO campaigns (id, organization_id, name, created_by) VALUES ($1, $2, 'Campaña B', $3)",
                            ids["campaign_b"], ids["org_b"], ids["owner_b"])
        await owner.execute("INSERT INTO analysis_quotas (organization_id, plan, monthly_analysis_limit)"
                            " VALUES ($1, 'pilot', 5), ($2, 'pilot', 5)", ids["org_a"], ids["org_b"])
        await owner.execute("INSERT INTO analysis_quota_usage (organization_id, period_start, consumed, released)"
                            " VALUES ($1, date_trunc('month', now())::date, 0, 0),"
                            " ($2, date_trunc('month', now())::date, 0, 0)", ids["org_a"], ids["org_b"])
        await owner.execute(
            "INSERT INTO analyses (id, organization_id, requested_by, idempotency_key, input_hash, prompt_version,"
            " model_requested, deterministic_report, status, lease_expires_at)"
            " VALUES ($1, $2, $3, 'key-poc-analysis-a', repeat('a', 64), 'v1', 'model-x', '{}'::jsonb, 'running',"
            " now() + interval '5 minutes')",
            ids["analysis_a"], ids["org_a"], ids["analyst_a"],
        )
        for event, org in (("event_a", "org_a"), ("event_b", "org_b")):
            await owner.execute(
                "INSERT INTO audit_events (organization_id, source, action, resource_type, resource_id)"
                " VALUES ($1, 'internal', 'quota.updated', 'analysis_quota', $2)",
                ids[org], str(ids[event]),
            )
    finally:
        await owner.close()
    return ids


async def set_subject(conn: asyncpg.Connection, subject: str | None) -> None:
    if subject is not None:
        await conn.execute("SELECT set_config('app.subject', $1, true)", subject)


async def scalar(conn, subject: str | None, sql: str, *args):  # type: ignore[no-untyped-def]
    async with conn.transaction():
        await set_subject(conn, subject)
        return await conn.fetchval(sql, *args)


async def denied(conn, subject: str | None, sql: str, *args) -> tuple[bool, str]:  # type: ignore[no-untyped-def]
    """True when the statement is refused by privilege, RLS or ownership (SQLSTATE 42501 family)."""
    try:
        async with conn.transaction():
            await set_subject(conn, subject)
            await conn.execute(sql, *args)
    except asyncpg.exceptions.InsufficientPrivilegeError as exc:
        return True, exc.message
    except asyncpg.exceptions.PostgresError as exc:
        return False, f"{type(exc).__name__}: {exc.message}"
    return False, "statement succeeded"


async def run_checks(ids: dict[str, uuid.UUID]) -> None:
    app = await connect("rckt_app")
    try:
        # 1. Role attributes and ownership.
        attrs = await app.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        check("app role is not superuser and has no BYPASSRLS", not attrs["rolsuper"] and not attrs["rolbypassrls"],
              f"super={attrs['rolsuper']} bypassrls={attrs['rolbypassrls']}")
        owned = await app.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user"
        )
        check("app role owns no table", owned == 0, f"owned tables: {owned}")

        # 2. Fail closed without identity.
        for table in ("organizations", "campaigns", "audit_events", "analyses"):
            count = await scalar(app, None, f"SELECT count(*) FROM public.{table}")
            check(f"no identity context: {table} returns no rows", count == 0, f"rows: {count}")

        # 3. Context does not survive into a later transaction on the same pooled connection.
        seen_with = await scalar(app, "sub-owner-a", "SELECT count(*) FROM public.organizations")
        seen_after = await scalar(app, None, "SELECT count(*) FROM public.organizations")
        check("context is transaction-local: next transaction on the same connection sees nothing",
              seen_with == 1 and seen_after == 0, f"with={seen_with} after={seen_after}")

        # 4. Session-scoped setting would leak. Demonstrates why is_local must be true.
        async with app.transaction():
            await app.execute("SELECT set_config('app.subject', 'sub-owner-a', false)")
        leaked = await app.fetchval("SELECT count(*) FROM public.organizations")
        check("diagnostic: a session-scoped setting WOULD leak (never used)", leaked == 1, f"rows seen: {leaked}")
        await app.execute("SELECT set_config('app.subject', '', false)")

        # 5. Cross-organization isolation.
        check("owner A sees only organization A",
              await scalar(app, "sub-owner-a", "SELECT count(*) FROM public.organizations") == 1)
        check("owner A sees campaigns of A only",
              await scalar(app, "sub-owner-a", "SELECT count(*) FROM public.campaigns") == 1)
        check("owner A cannot read campaign of B",
              await scalar(app, "sub-owner-a", "SELECT count(*) FROM public.campaigns WHERE organization_id = $1",
                           ids["org_b"]) == 0)
        refused, detail = await denied(
            app, "sub-owner-a",
            "INSERT INTO public.campaigns (organization_id, name, created_by) VALUES ($1, 'x', $2)",
            ids["org_b"], ids["owner_a"],
        )
        check("owner A cannot insert a campaign into B", refused, detail)
        refused, detail = await denied(
            app, "sub-owner-a",
            "INSERT INTO public.campaigns (organization_id, name, created_by) VALUES ($1, 'Campaña A nueva', $2)",
            ids["org_a"], ids["owner_a"],
        )
        check("owner A can insert a campaign into A", not refused, detail)

        # 6. Role without permission for the operation.
        check("client_viewer reads campaigns of A",
              await scalar(app, "sub-viewer-a", "SELECT count(*) FROM public.campaigns") >= 1)
        refused, detail = await denied(
            app, "sub-viewer-a",
            "INSERT INTO public.campaigns (organization_id, name, created_by) VALUES ($1, 'x', $2)",
            ids["org_a"], ids["viewer_a"],
        )
        check("client_viewer cannot create a campaign", refused, detail)
        refused, detail = await denied(
            app, "sub-viewer-a",
            "INSERT INTO public.analyses (organization_id, requested_by, idempotency_key, input_hash, prompt_version,"
            " model_requested, deterministic_report, status, lease_expires_at)"
            " VALUES ($1, $2, 'key-viewer-attempt', repeat('b', 64), 'v1', 'm', '{}'::jsonb, 'running', now())",
            ids["org_a"], ids["viewer_a"],
        )
        check("client_viewer cannot create an analysis", refused, detail)
        check("analyst cannot read the audit trail of A",
              await scalar(app, "sub-analyst-a", "SELECT count(*) FROM public.audit_events WHERE organization_id = $1",
                           ids["org_a"]) == 0)
        check("owner A reads the audit trail of A",
              await scalar(app, "sub-owner-a", "SELECT count(*) FROM public.audit_events WHERE organization_id = $1",
                           ids["org_a"]) >= 1)
        check("owner A cannot read the audit trail of B",
              await scalar(app, "sub-owner-a", "SELECT count(*) FROM public.audit_events WHERE organization_id = $1",
                           ids["org_b"]) == 0)

        # 7. GET /me keeps working for a user with several organizations, and shows only their own memberships.
        memberships = await scalar(app, "sub-multi", "SELECT count(*) FROM public.organization_memberships")
        orgs = await scalar(app, "sub-multi", "SELECT count(*) FROM public.organizations")
        check("GET /me: user with two organizations sees both memberships and both organizations",
              memberships == 2 and orgs == 2, f"memberships={memberships} organizations={orgs}")
        other = await scalar(app, "sub-viewer-a", "SELECT count(*) FROM public.organization_memberships")
        check("GET /me: a single-organization user sees only their own membership", other == 1, f"rows: {other}")

        # 8. Quota ledger: the conditional consumption must work for analysts and must not for viewers.
        consume = ("UPDATE public.analysis_quota_usage AS u SET consumed = u.consumed + 1"
                   " FROM public.analysis_quotas AS q"
                   " WHERE u.organization_id = $1 AND u.period_start = date_trunc('month', now())::date"
                   " AND q.organization_id = u.organization_id AND u.consumed < q.monthly_analysis_limit")
        async with app.transaction():
            await set_subject(app, "sub-analyst-a")
            analyst_rows = (await app.execute(consume, ids["org_a"]))
        check("analyst consumes a quota unit of A (conditional UPDATE with join)", analyst_rows == "UPDATE 1",
              analyst_rows)
        async with app.transaction():
            await set_subject(app, "sub-viewer-a")
            viewer_rows = (await app.execute(consume, ids["org_a"]))
        check("client_viewer cannot consume a unit", viewer_rows == "UPDATE 0", viewer_rows)

        # 9. Privilege boundaries of the runtime role.
        refused, detail = await denied(app, "sub-owner-a", "TRUNCATE public.campaigns")
        check("app role cannot TRUNCATE a business table", refused, detail)
        refused, detail = await denied(app, "sub-owner-a", "TRUNCATE public.audit_events")
        check("app role cannot TRUNCATE audit_events", refused, detail)
        refused, detail = await denied(app, "sub-owner-a",
                                       "ALTER TABLE public.audit_events DISABLE TRIGGER trg_audit_events_append_only")
        check("app role cannot disable the append-only trigger", refused, detail)
        refused, detail = await denied(app, "sub-owner-a", "ALTER TABLE public.campaigns ADD COLUMN poc_x int")
        check("app role cannot alter a table", refused, detail)
        refused, detail = await denied(app, "sub-owner-a", "UPDATE public.audit_events SET action = action")
        check("app role cannot UPDATE audit_events", refused, detail)
        refused, detail = await denied(app, "sub-owner-a", "DELETE FROM public.audit_events")
        check("app role cannot DELETE audit_events", refused, detail)
        refused, detail = await denied(app, "sub-owner-a", "CREATE POLICY poc_bad ON public.campaigns FOR SELECT USING (true)")
        check("app role cannot create policies", refused, detail)
        refused, detail = await denied(app, "sub-owner-a", "CREATE TABLE public.poc_new (id int)")
        check("app role cannot create tables in public", refused, detail)
        refused, detail = await denied(app, "sub-owner-a", "SET ROLE rckt_owner")
        check("app role cannot assume the owner role", refused, detail)
        for role in ("anon", "authenticated"):
            privileges = await scalar(app, None,
                                      "SELECT count(*) FROM information_schema.role_table_grants"
                                      " WHERE grantee = $1 AND table_schema = 'public'", role)
            check(f"no table grants to {role}", privileges == 0, f"grants: {privileges}")

        # 10. The authorized write path: a user event, attributed to the caller, inside their organization.
        refused, detail = await denied(
            app, "sub-analyst-a",
            "INSERT INTO public.audit_events (organization_id, source, actor_user_id, action, resource_type, resource_id)"
            " VALUES ($1, 'user', $2, 'analysis.created', 'analysis', 'poc-1')", ids["org_a"], ids["analyst_a"],
        )
        check("authorized user audit event is accepted", not refused, detail)
        refused, detail = await denied(
            app, "sub-analyst-a",
            "INSERT INTO public.audit_events (organization_id, source, actor_user_id, action, resource_type, resource_id)"
            " VALUES ($1, 'user', $2, 'analysis.created', 'analysis', 'poc-2')", ids["org_a"], ids["owner_a"],
        )
        check("audit event attributed to someone else is refused", refused, detail)
        refused, detail = await denied(
            app, "sub-analyst-a",
            "INSERT INTO public.audit_events (organization_id, source, action, resource_type, resource_id)"
            " VALUES ($1, 'internal', 'quota.updated', 'analysis_quota', 'poc-3')", ids["org_a"],
        )
        check("internal audit event from the app role is refused", refused, detail)
    finally:
        await app.close()


def report() -> int:
    width = max(len(name) for name, _, _ in results)
    for name, status, detail in results:
        suffix = f"  [{detail}]" if detail and status == FAIL else ""
        print(f"{status}  {name.ljust(width)}{suffix}")
    failures = [name for name, status, _ in results if status == FAIL]
    print(f"\n{len(results) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


async def main() -> int:
    await setup_roles()
    await apply_migrations()
    ids = await seed()
    await run_checks(ids)
    return report()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
