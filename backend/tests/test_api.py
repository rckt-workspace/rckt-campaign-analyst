"""HTTP contract of the analysis API: the legacy route is retired, and the persisted endpoint keeps the safety checks.

Authentication and organization checks are covered in test_authz_api.py; quota in test_quota.py.
"""

import httpx
from fastapi.testclient import TestClient

from app.db.repositories import MembershipRepository, OrganizationRepository, UserRepository
from app.domain.access import Role
from app.main import app
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import make_token
from tests.conftest import run
from tests.test_analysis_api import provider  # noqa: F401  (pytest fixtures)

client = TestClient(app)

BASE_DATA = {
    "rawInput": "Campaña: Test",
    "campaign": "Test",
    "investment": 1000,
    "clicks": 100,
    "impressions": 2000,
}
LEGACY_PATH = "/api/v1/organizations/00000000-0000-0000-0000-000000000001/campaigns/analyze"


def _analyst(database):  # type: ignore[no-untyped-def]
    """An analyst in a fresh organization. Returns (headers for one request, the persisted analyses path)."""

    async def create():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            organization = await OrganizationRepository(session).create(name="Test", slug="test-org")
            user = await UserRepository(session).create(external_subject="sub-contract", email="contract@example.com")
            await MembershipRepository(session).add(organization_id=organization.id, user_id=user.id, role=Role.ANALYST)
            return organization.id

    organization_id = run(create())
    headers = {"Authorization": f"Bearer {make_token('sub-contract')}", "Idempotency-Key": "key-contract-0001"}
    return headers, f"/api/v1/organizations/{organization_id}/analyses"


def test_health_without_provider_key():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_the_legacy_analysis_route_is_retired_and_never_calls_the_provider(provider):  # type: ignore[no-untyped-def]
    anonymous = client.post(LEGACY_PATH, json=BASE_DATA)
    authenticated = client.post(LEGACY_PATH, json=BASE_DATA, headers={"Authorization": f"Bearer {make_token('sub-x')}"})

    assert anonymous.status_code == 410 and anonymous.json() == {"detail": "ENDPOINT_RETIRED"}
    assert authenticated.status_code == 410
    assert provider.calls == 0


def test_the_persisted_endpoint_rejects_invalid_input_before_any_provider_call(api_client, database, provider):  # type: ignore[no-untyped-def]
    headers, url = _analyst(database)

    assert api_client.post(url, json={"data": {"rawInput": "x", "ctr": "non-number"}}, headers=headers).status_code == 422
    assert api_client.post(url, json={"data": {"rawInput": "x", "unexpected": 1}}, headers=headers).status_code == 422
    assert api_client.post(url, json={"data": {"rawInput": "x" * 12001}}, headers=headers).status_code == 422
    assert provider.calls == 0


def test_a_provider_failure_is_stored_without_leaking_its_message(api_client, database, provider):  # type: ignore[no-untyped-def]
    headers, url = _analyst(database)
    provider.replies = [httpx.ConnectError("SECRET-MARKER-DO-NOT-ECHO"), httpx.ConnectError("SECRET-MARKER-DO-NOT-ECHO")]

    response = api_client.post(url, json={"data": BASE_DATA}, headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["error_code"]) == ("failed", "AI_NETWORK")
    assert "SECRET-MARKER" not in response.text
    assert all("SECRET-MARKER" not in str(attempt) for attempt in body["attempts"])
    assert body["analysis_markdown"] is None
