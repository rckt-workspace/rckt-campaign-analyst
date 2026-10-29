"""Lifecycle of the bound identity (app.db.session.bind_identity), exercised through the real application with rckt_app.

The identity is bound by the verified-identity dependency. These tests check that it never outlives its request, even when
the transport runs the request in the caller's own task (httpx ASGI transport does), and that concurrent, failed,
rejected and cancelled requests never leave a binding for the next one.

Setup and assertions on the database use the owner-equivalent fixture; every request goes through rckt_app.
"""

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text

from app.api.deps import get_verified_identity  # noqa: F401  (documents the dependency under test)
from app.db.session import Database, bound_identity, identity_scope
from app.db.repositories import MembershipRepository
from app.domain.access import Role
from app.main import app
from tests.api_fixtures import token_verifier  # noqa: F401  (pytest fixture)
from tests.conftest import run
from tests.test_campaign_api import seed
from tests.test_db_security import app_database, app_url, token_for  # noqa: F401  (pytest fixtures)

ME = "/api/v1/me"


@pytest.fixture
def client(app_database, token_verifier):  # type: ignore[no-untyped-def]
    app.state.database = app_database
    app.state.token_verifier = token_verifier
    try:
        transport = httpx.ASGITransport(app=app)
        yield httpx.AsyncClient(transport=transport, base_url="http://test")
    finally:
        app.state.database = None
        app.state.token_verifier = None


def _two_users(database):  # type: ignore[no-untyped-def]
    org_a, _ = seed(database, role=Role.OWNER, subject="life-a", org_slug="life-org-a")
    org_b, _ = seed(database, role=Role.OWNER, subject="life-b", org_slug="life-org-b")
    return org_a, org_b


def _slugs(response: httpx.Response) -> set[str]:
    return {item["slug"] for item in response.json()["organizations"]}


def test_no_identity_remains_bound_in_the_caller_after_a_valid_request(database, client):  # type: ignore[no-untyped-def]
    _two_users(database)

    async def scenario():  # type: ignore[no-untyped-def]
        response = await client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-a')}"})
        return response.status_code, bound_identity()

    status, leftover = run(scenario())
    assert status == 200
    assert leftover is None, "the identity leaked into the caller's context after the request"


def token_for_value(subject: str) -> str:
    from tests.auth_helpers import make_token

    return make_token(subject)


def test_the_next_request_with_another_user_sees_only_that_users_organizations(database, client):  # type: ignore[no-untyped-def]
    org_a, org_b = _two_users(database)

    async def scenario():  # type: ignore[no-untyped-def]
        first = await client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-a')}"})
        second = await client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-b')}"})
        return first, second

    first, second = run(scenario())
    assert _slugs(first) == {"life-org-a"}
    assert _slugs(second) == {"life-org-b"}


def test_an_invalid_token_after_a_valid_request_binds_nothing_and_sees_nothing(database, client):  # type: ignore[no-untyped-def]
    _two_users(database)

    async def scenario():  # type: ignore[no-untyped-def]
        await client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-a')}"})
        rejected = await client.get(ME, headers={"Authorization": "Bearer not-a-token"})
        return rejected.status_code, bound_identity()

    status, leftover = run(scenario())
    assert status == 401
    assert leftover is None


def test_a_request_without_token_after_a_valid_one_sees_no_data(database, client):  # type: ignore[no-untyped-def]
    _two_users(database)

    async def scenario():  # type: ignore[no-untyped-def]
        await client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-a')}"})
        return (await client.get(ME)).status_code, bound_identity()

    status, leftover = run(scenario())
    assert status == 401
    assert leftover is None


def test_an_exception_inside_the_operation_releases_the_identity(database, client, monkeypatch):  # type: ignore[no-untyped-def]
    org_a, _ = _two_users(database)

    async def broken(self, user_id):  # type: ignore[no-untyped-def]
        raise RuntimeError("injected failure inside the operation")

    async def scenario():  # type: ignore[no-untyped-def]
        monkeypatch.setattr(MembershipRepository, "list_for_user", broken)
        with pytest.raises(RuntimeError):
            await client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-a')}"})
        leftover = bound_identity()
        monkeypatch.undo()
        after = await client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-b')}"})
        return leftover, after

    leftover, after = run(scenario())
    assert leftover is None
    assert _slugs(after) == {"life-org-b"}


def test_concurrent_requests_each_see_only_their_own_identity(database, client):  # type: ignore[no-untyped-def]
    _two_users(database)

    async def scenario():  # type: ignore[no-untyped-def]
        return await asyncio.gather(
            client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-a')}"}),
            client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-b')}"}),
            client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-a')}"}),
        )

    first, second, third = run(scenario())
    assert _slugs(first) == _slugs(third) == {"life-org-a"}
    assert _slugs(second) == {"life-org-b"}


def test_a_cancelled_request_releases_the_identity_for_the_next_one(database, client, monkeypatch):  # type: ignore[no-untyped-def]
    _two_users(database)

    async def slow(self, user_id):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30)

    async def scenario():  # type: ignore[no-untyped-def]
        monkeypatch.setattr(MembershipRepository, "list_for_user", slow)
        task = asyncio.ensure_future(client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-a')}"}))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        monkeypatch.undo()
        leftover = bound_identity()
        after = await client.get(ME, headers={"Authorization": f"Bearer {token_for_value('life-b')}"})
        return leftover, after

    leftover, after = run(scenario())
    assert leftover is None
    assert _slugs(after) == {"life-org-b"}


def test_a_task_started_inside_a_scope_inherits_it_and_a_later_scope_does_not(app_database):  # type: ignore[no-untyped-def]
    """Child tasks copy the context: a task created during a request sees that request's identity. That is intended.
    What must not happen is the identity surviving the scope that created it."""

    async def child_sees() -> str | None:
        return bound_identity()

    async def scenario():  # type: ignore[no-untyped-def]
        with identity_scope("child-owner"):
            inherited = await asyncio.ensure_future(child_sees())
        outside = bound_identity()
        return inherited, outside

    inherited, outside = run(scenario())
    assert inherited == "child-owner"
    assert outside is None


def test_a_rollback_inside_a_session_leaves_no_identity_for_the_pooled_connection(app_database, raw_engine):  # type: ignore[no-untyped-def]
    async def scenario():  # type: ignore[no-untyped-def]
        with pytest.raises(RuntimeError):
            with identity_scope("rollback-owner"):
                async with app_database.session() as session:
                    await session.execute(text("SELECT 1"))
                    raise RuntimeError("abort after binding")
        async with app_database.session() as session:
            setting = (await session.execute(text("SELECT current_setting('app.subject', true)"))).scalar_one()
            visible = (await session.execute(text("SELECT count(*) FROM public.organizations"))).scalar_one()
        return setting, visible

    setting, visible = run(scenario())
    assert not setting
    assert visible == 0
