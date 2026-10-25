"""Operator procedures: organizations and memberships are granted only through the CLI, never through HTTP."""

import pytest

from app import cli
from app.db.repositories import MembershipRepository, OrganizationRepository, UserRepository
from app.domain.access import Role
from tests.conftest import run


@pytest.fixture
def cli_database(monkeypatch, test_database_url, database):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    return database


def test_new_user_gets_identity_but_no_organization_access(cli_database):  # type: ignore[no-untyped-def]
    assert run(cli.create_organization("Cliente Uno", "cliente-uno")).startswith("created organization cliente-uno")
    message = run(cli.provision_user("sub-fresh", "fresh@cliente.com"))
    assert "no organization access granted" in message

    async def memberships():  # type: ignore[no-untyped-def]
        async with cli_database.session() as session:
            user = await UserRepository(session).get_by_subject("sub-fresh")
            return await MembershipRepository(session).list_for_user(user.id)

    assert run(memberships()) == []


def test_owner_is_granted_only_by_the_operator_procedure(cli_database):  # type: ignore[no-untyped-def]
    run(cli.create_organization("RCKT Interno", "rckt-internal"))
    run(cli.provision_user("sub-owner", "owner@rckt.es"))
    assert "granted owner" in run(cli.grant_membership("rckt-internal", Role.OWNER, None, "owner@rckt.es"))

    async def role():  # type: ignore[no-untyped-def]
        async with cli_database.session() as session:
            user = await UserRepository(session).get_by_subject("sub-owner")
            rows = await MembershipRepository(session).list_for_user(user.id)
            return [(organization.slug, membership_role) for organization, membership_role in rows]

    assert run(role()) == [("rckt-internal", "owner")]


def test_duplicate_membership_is_refused(cli_database):  # type: ignore[no-untyped-def]
    run(cli.create_organization("Cliente Dos", "cliente-dos"))
    run(cli.provision_user("sub-dup", "dup@cliente.com"))
    run(cli.grant_membership("cliente-dos", Role.ANALYST, "sub-dup", None))
    with pytest.raises(SystemExit):
        run(cli.grant_membership("cliente-dos", Role.OWNER, "sub-dup", None))


def test_unknown_user_or_organization_is_refused(cli_database):  # type: ignore[no-untyped-def]
    run(cli.create_organization("Cliente Tres", "cliente-tres"))
    with pytest.raises(SystemExit):
        run(cli.grant_membership("cliente-tres", Role.ANALYST, None, "nobody@cliente.com"))
    with pytest.raises(SystemExit):
        run(cli.grant_membership("no-existe", Role.ANALYST, "sub-x", None))


def test_grant_requires_exactly_one_identifier(cli_database):  # type: ignore[no-untyped-def]
    run(cli.create_organization("Cliente Cuatro", "cliente-cuatro"))
    with pytest.raises(SystemExit):
        run(cli.grant_membership("cliente-cuatro", Role.ANALYST, "sub-x", "x@cliente.com"))


def test_cli_refuses_without_database_configuration(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    with pytest.raises(SystemExit):
        cli._database()
