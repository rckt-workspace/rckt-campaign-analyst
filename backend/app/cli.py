"""Operator procedures. Run on the server, never exposed over HTTP.

Each command calls one SECURITY DEFINER function of the database (migration 0009). The connection is DATABASE_OPERATOR_URL,
a login of the rckt_operator role, and nothing else: DATABASE_URL and DATABASE_MIGRATION_URL are never used here. The
functions validate their parameters, change the data and write the audit event in one transaction, and record the database
login (session_user) in audit_events.db_principal, so each operator stays traceable.

Examples (DATABASE_OPERATOR_URL must point to the target database):
  python -m app.cli create-organization --name "Cliente Uno" --slug cliente-uno
  python -m app.cli grant-membership --organization-slug cliente-uno --email ana@example.com --role analyst
  python -m app.cli grant-membership --organization-slug rckt-internal --email owner@example.com --role owner
  python -m app.cli set-analysis-quota --organization-slug cliente-uno --plan pilot --monthly-limit 50

A user appears in app_users after their first successful sign-in. Until then, grant-membership cannot find them by email;
the operator can also use --subject with the provider's user id.
"""

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import DatabaseSettings, get_database_settings
from app.db.session import Database
from app.domain.access import Role
from app.domain.quota import PLAN_VALUES

# Messages the functions raise on purpose. Anything else is answered without the driver's text, which can carry details.
REFUSALS = {
    "ORGANIZATION_NOT_FOUND": "organization not found",
    "USER_NOT_FOUND": "user not found: the person must sign in once first, or provision-user must run",
    "MEMBERSHIP_EXISTS": "membership already exists; change roles with a new procedure, not by duplication",
    "AMBIGUOUS_EMAIL": "more than one user has that email: grant with --subject instead",
    "SUBJECT_OR_EMAIL_REQUIRED": "provide exactly one of --subject or --email",
    "INVALID_NAME": "the name is required and has at most 120 characters",
    "INVALID_SLUG": "the slug must be lowercase letters, digits and hyphens, at most 63 characters",
    "INVALID_SUBJECT": "the subject is required and has at most 255 characters",
    "INVALID_EMAIL": "the email has at most 320 characters",
    "INVALID_ROLE": "the role must be owner, analyst or client_viewer",
    "INVALID_PLAN": "the plan must be pilot, standard or enterprise",
    "INVALID_LIMIT": "the monthly limit must be zero or more",
}
REFUSED = "the database refused the operation; nothing was changed"


def _database() -> Database:
    settings = get_database_settings("DATABASE_OPERATOR_URL")
    if settings is None:
        raise SystemExit("Set DATABASE_OPERATOR_URL to the operator login of the target database.")
    return Database(DatabaseSettings(
        url=settings.url, pool_size=1, max_overflow=0, connect_timeout_seconds=settings.connect_timeout_seconds,
    ))


def _refusal(driver_message: str) -> str:
    for code, message in REFUSALS.items():
        if code in driver_message:
            return message
    if "uq_organizations_slug" in driver_message:
        return "that slug is already in use"
    return REFUSED


async def _call(database: Database, statement: str, params: dict) -> tuple:  # type: ignore[type-arg]
    """Runs one function call in its own transaction. A refusal raises SystemExit with a fixed message."""
    try:
        async with database.session() as session:
            row = (await session.execute(text(statement), params)).one()
            return tuple(row)
    except DBAPIError as exc:
        raise SystemExit(_refusal(str(exc.orig))) from None


async def create_organization(name: str, slug: str) -> str:
    database = _database()
    try:
        (organization_id,) = await _call(database, "SELECT public.operator_create_organization(:name, :slug)",
                                         {"name": name, "slug": slug})
        return f"created organization {slug} ({organization_id})"
    finally:
        await database.dispose()


async def provision_user(subject: str, email: str | None) -> str:
    database = _database()
    try:
        (user_id,) = await _call(database, "SELECT public.operator_provision_user(:subject, :email)",
                                 {"subject": subject, "email": email or None})
        return f"identity ready for user {user_id} (no organization access granted)"
    finally:
        await database.dispose()


async def grant_membership(organization_slug: str, role: Role, subject: str | None, email: str | None) -> str:
    if (subject is None) == (email is None):
        raise SystemExit("Provide exactly one of --subject or --email.")
    database = _database()
    try:
        (membership_id,) = await _call(
            database,
            "SELECT public.operator_grant_membership(:slug, :subject, :email, :role)",
            {"slug": organization_slug, "subject": subject, "email": email, "role": role.value},
        )
        return f"granted {role.value} in {organization_slug} (membership {membership_id})"
    finally:
        await database.dispose()


async def set_analysis_quota(organization_slug: str, plan: str, monthly_limit: int) -> str:
    """Creates or replaces the organization's quota. The limit is a count of analyses per UTC month, never money."""
    database = _database()
    try:
        changed, _, _ = await _call(
            database,
            "SELECT changed, previous_plan, previous_monthly_analysis_limit"
            " FROM public.operator_set_analysis_quota(:slug, :plan, :limit)",
            {"slug": organization_slug, "plan": plan, "limit": monthly_limit},
        )
        if not changed:
            # Nothing changed: no write and no event. Repeating the same procedure is not a new change.
            return f"quota for {organization_slug} unchanged: plan {plan}, {monthly_limit} analyses per UTC month"
        return f"quota for {organization_slug}: plan {plan}, {monthly_limit} analyses per UTC month"
    finally:
        await database.dispose()


def _non_negative_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number") from None
    if value < 0:
        raise argparse.ArgumentTypeError("must be zero or more")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="RCKT operator procedures")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-organization")
    create.add_argument("--name", required=True)
    create.add_argument("--slug", required=True)

    provision = commands.add_parser("provision-user")
    provision.add_argument("--subject", required=True)
    provision.add_argument("--email")

    grant = commands.add_parser("grant-membership")
    grant.add_argument("--organization-slug", required=True)
    grant.add_argument("--role", required=True, type=Role)
    grant.add_argument("--subject")
    grant.add_argument("--email")

    quota = commands.add_parser("set-analysis-quota")
    quota.add_argument("--organization-slug", required=True)
    quota.add_argument("--plan", required=True, choices=PLAN_VALUES)
    quota.add_argument("--monthly-limit", required=True, type=_non_negative_int)

    args = parser.parse_args(argv)
    if args.command == "set-analysis-quota":
        message = asyncio.run(set_analysis_quota(args.organization_slug, args.plan, args.monthly_limit))
    elif args.command == "create-organization":
        message = asyncio.run(create_organization(args.name, args.slug))
    elif args.command == "provision-user":
        message = asyncio.run(provision_user(args.subject, args.email))
    else:
        message = asyncio.run(grant_membership(args.organization_slug, args.role, args.subject, args.email))
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
