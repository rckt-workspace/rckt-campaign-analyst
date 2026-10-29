"""Read-only privilege audit of the RCKT PostgreSQL schema.

Reads catalogs only, inside a read-only transaction. It never writes, never creates roles and never prints connection
strings or secrets. The database name must end in _test, as for the test suite.

Usage (from backend/, with the venv Python):
    DATABASE_AUDIT_URL=postgresql+asyncpg://USER:PASSWORD@HOST:PORT/rckt_test python ../scripts/db_security_audit.py

The URL is read from the environment only. The output names roles, tables and flags; it does not include the URL.
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

ENTERPRISE_TABLES = (
    "organizations",
    "app_users",
    "organization_memberships",
    "campaigns",
    "campaign_metric_entries",
    "analyses",
    "analysis_usage",
    "analysis_quotas",
    "analysis_quota_usage",
    "audit_events",
)
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES")
ROLE_NAMES = ("anon", "authenticated", "service_role", "PUBLIC")


def require_test_database(url: str) -> None:
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        raise SystemExit("Refusing to audit: the database name must end in _test.")


async def audit(engine) -> None:  # type: ignore[no-untyped-def]
    async with engine.connect() as connection:
        await connection.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))

        current = (
            await connection.execute(
                text("SELECT current_user AS u, r.rolsuper, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user")
            )
        ).one()
        print(f"current role: super={current.rolsuper} bypassrls={current.rolbypassrls} (name not printed)")

        print("\n== roles (cluster, excluding pg_*)")
        roles = (
            await connection.execute(
                text("SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY 1")
            )
        ).all()
        for row in roles:
            print(f"  {row.rolname}: super={row.rolsuper} bypassrls={row.rolbypassrls} login={row.rolcanlogin}")

        print("\n== tables")
        tables = (
            await connection.execute(
                text(
                    "SELECT c.relname, pg_get_userbyid(c.relowner) AS owner, c.relrowsecurity, c.relforcerowsecurity,"
                    " (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies"
                    " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = ANY(:names) ORDER BY 1"
                ),
                {"names": list(ENTERPRISE_TABLES)},
            )
        ).all()
        for row in tables:
            print(
                f"  {row.relname}: owner={row.owner} rls={row.relrowsecurity} force_rls={row.relforcerowsecurity}"
                f" policies={row.policies}"
            )

        print("\n== privileges per role (has_table_privilege)")
        existing = {row.rolname for row in roles}
        grantees = [name for name in ROLE_NAMES if name == "PUBLIC" or name in existing]
        print(f"  grantees checked: {', '.join(grantees)} (PUBLIC always)")
        for table in [row.relname for row in tables]:
            for grantee in grantees:
                granted = []
                for privilege in PRIVILEGES:
                    if grantee == "PUBLIC":
                        allowed = (
                            await connection.execute(
                                text("SELECT has_table_privilege('public', :t, :p)"),
                                {"t": f"public.{table}", "p": privilege},
                            )
                        ).scalar_one()
                    else:
                        allowed = (
                            await connection.execute(
                                text("SELECT has_table_privilege(:r, :t, :p)"),
                                {"r": grantee, "t": f"public.{table}", "p": privilege},
                            )
                        ).scalar_one()
                    if allowed:
                        granted.append(privilege)
                if granted:
                    print(f"  {table} <- {grantee}: {', '.join(granted)}")
        print("  (a table with no line above grants nothing to the checked grantees)")

        print("\n== triggers on enterprise tables (non-internal)")
        triggers = (
            await connection.execute(
                text(
                    "SELECT c.relname, t.tgname, t.tgenabled, p.proname FROM pg_trigger t"
                    " JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " JOIN pg_proc p ON p.oid = t.tgfoid"
                    " WHERE NOT t.tgisinternal AND n.nspname = 'public' AND c.relname = ANY(:names) ORDER BY 1, 2"
                ),
                {"names": list(ENTERPRISE_TABLES)},
            )
        ).all()
        for row in triggers:
            print(f"  {row.relname}: {row.tgname} (enabled={row.tgenabled}) -> {row.proname}()")

        print("\n== functions in public")
        functions = (
            await connection.execute(
                text(
                    "SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner) AS owner FROM pg_proc p"
                    " JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' ORDER BY 1"
                )
            )
        ).all()
        for row in functions:
            print(f"  {row.proname}: security_definer={row.prosecdef} owner={row.owner}")
        if not functions:
            print("  (none)")

        print("\n== schema public")
        create_for_public = (
            await connection.execute(text("SELECT has_schema_privilege('public', 'public', 'CREATE')"))
        ).scalar_one()
        print(f"  CREATE granted to PUBLIC: {create_for_public}")


async def main() -> int:
    url = os.environ.get("DATABASE_AUDIT_URL", "").strip()
    if not url:
        print("DATABASE_AUDIT_URL is not set.", file=sys.stderr)
        return 2
    if not url.startswith("postgresql+asyncpg://"):
        print("DATABASE_AUDIT_URL must use postgresql+asyncpg://", file=sys.stderr)
        return 2
    require_test_database(url)
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        await audit(engine)
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
