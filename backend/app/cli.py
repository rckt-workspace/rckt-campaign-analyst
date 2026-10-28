"""Operator procedures. Run on the server, never exposed over HTTP.

Examples (DATABASE_URL or DATABASE_MIGRATION_URL must point to the target database):
  python -m app.cli create-organization --name "Cliente Uno" --slug cliente-uno
  python -m app.cli grant-membership --organization-slug cliente-uno --email ana@cliente.com --role analyst
  python -m app.cli grant-membership --organization-slug rckt-internal --email owner@rckt.es --role owner
  python -m app.cli set-analysis-quota --organization-slug cliente-uno --plan pilot --monthly-limit 50

A user appears in app_users after their first successful sign-in. Until then, grant-membership
cannot find them by email; the operator can also use --subject with the provider's user id.
"""

import argparse
import asyncio
import os
import sys
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import DatabaseSettings, get_database_settings
from app.db.models import AnalysisQuota, AppUser, Organization
from app.db.repositories import MembershipRepository, OrganizationRepository, UserRepository
from app.db.session import Database
from app.db.audit import insert_audit_event
from app.domain.access import Role
from app.domain.audit import AuditDraft
from app.domain.quota import PLAN_VALUES


def _database() -> Database:
    env_name = "DATABASE_MIGRATION_URL" if os.environ.get("DATABASE_MIGRATION_URL") else "DATABASE_URL"
    settings = get_database_settings(env_name)
    if settings is None:
        raise SystemExit("Set DATABASE_URL (or DATABASE_MIGRATION_URL) to the target database.")
    return Database(DatabaseSettings(
        url=settings.url, pool_size=1, max_overflow=0, connect_timeout_seconds=settings.connect_timeout_seconds,
    ))


async def create_organization(name: str, slug: str) -> str:
    database = _database()
    try:
        async with database.session() as session:
            organization = await OrganizationRepository(session).create(name=name, slug=slug)
            return f"created organization {organization.slug} ({organization.id})"
    finally:
        await database.dispose()


async def provision_user(subject: str, email: str | None) -> str:
    database = _database()
    try:
        async with database.session() as session:
            user = await UserRepository(session).get_or_create_by_subject(external_subject=subject, email=email)
            return f"identity ready for user {user.id} (no organization access granted)"
    finally:
        await database.dispose()


async def grant_membership(organization_slug: str, role: Role, subject: str | None, email: str | None) -> str:
    if (subject is None) == (email is None):
        raise SystemExit("Provide exactly one of --subject or --email.")
    database = _database()
    try:
        async with database.session() as session:
            organization = await session.scalar(select(Organization).where(Organization.slug == organization_slug))
            if organization is None:
                raise SystemExit("organization not found")
            if subject is not None:
                user = await UserRepository(session).get_by_subject(subject)
            else:
                user = await session.scalar(select(AppUser).where(AppUser.email == email.strip().lower()))
            if user is None:
                raise SystemExit("user not found: the person must sign in once first, or provision-user must run")
            existing = await MembershipRepository(session).get_for_user(organization_id=organization.id, user_id=user.id)
            if existing is not None:
                raise SystemExit("membership already exists; change roles with a new procedure, not by duplication")
            membership = await MembershipRepository(session).add(organization_id=organization.id, user_id=user.id, role=role)
            await insert_audit_event(session, AuditDraft(
                organization_id=organization.id,
                action="membership.granted",
                resource_id=str(membership.id),
                source="operator_cli",
                details={"role": role.value},
            ))
            return f"granted {role.value} to user {user.id} in {organization.slug}"
    finally:
        await database.dispose()


async def set_analysis_quota(organization_slug: str, plan: str, monthly_limit: int) -> str:
    """Creates or replaces the organization's quota. The limit is a count of analyses per UTC month, never money."""
    database = _database()
    try:
        async with database.session() as session:
            organization = await session.scalar(select(Organization).where(Organization.slug == organization_slug))
            if organization is None:
                raise SystemExit("organization not found")
            current = await session.scalar(
                select(AnalysisQuota).where(AnalysisQuota.organization_id == organization.id).with_for_update()
            )
            if current is not None and (current.plan, current.monthly_analysis_limit) == (plan, monthly_limit):
                # Nothing changed: no write and no event. Repeating the same procedure is not a new change.
                return f"quota for {organization.slug} unchanged: plan {plan}, {monthly_limit} analyses per UTC month"
            await session.execute(
                pg_insert(AnalysisQuota)
                .values(organization_id=organization.id, plan=plan, monthly_analysis_limit=monthly_limit)
                .on_conflict_do_update(
                    index_elements=[AnalysisQuota.organization_id],
                    set_={"plan": plan, "monthly_analysis_limit": monthly_limit, "updated_at": func.now()},
                )
            )
            # Same transaction as the change: if the event cannot be written, the quota is not changed either.
            await insert_audit_event(session, AuditDraft(
                organization_id=organization.id,
                action="quota.updated",
                resource_id=str(organization.id),
                source="operator_cli",
                details={
                    "plan": plan,
                    "monthly_analysis_limit": monthly_limit,
                    "previous_plan": None if current is None else current.plan,
                    "previous_monthly_analysis_limit": None if current is None else current.monthly_analysis_limit,
                },
            ))
            return f"quota for {organization.slug}: plan {plan}, {monthly_limit} analyses per UTC month"
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
