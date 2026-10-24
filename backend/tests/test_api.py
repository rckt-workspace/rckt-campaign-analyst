"""HTTP contract of the campaign analysis endpoint. Authentication and organization checks are covered in test_authz_api.py."""

from fastapi.testclient import TestClient

from app.api import routes
from app.db.repositories import MembershipRepository, OrganizationRepository, UserRepository
from app.domain.access import Role
from app.main import app
from tests.api_fixtures import api_client, provider_calls, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import make_token
from tests.conftest import run

client = TestClient(app)

BASE_PAYLOAD = {
    "rawInput": "Campaña: Test",
    "campaign": "Test",
    "investment": 1000,
    "clicks": 100,
    "impressions": 2000,
}


def _analyst_headers(database) -> tuple[dict, str]:  # type: ignore[no-untyped-def]
    async def create():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            organization = await OrganizationRepository(session).create(name="Test", slug="test-org")
            user = await UserRepository(session).create(external_subject="sub-contract", email="contract@example.com")
            await MembershipRepository(session).add(organization_id=organization.id, user_id=user.id, role=Role.ANALYST)
            return organization.id

    organization_id = run(create())
    headers = {"Authorization": f"Bearer {make_token('sub-contract')}"}
    return headers, f"/api/v1/organizations/{organization_id}/campaigns/analyze"


def test_health_without_provider_key():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_analysis_requires_identity_before_validation_or_cost():
    response = client.post("/api/v1/organizations/00000000-0000-0000-0000-000000000001/campaigns/analyze", json=BASE_PAYLOAD)
    assert response.status_code == 401


def test_input_rejects_invalid_payload(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    headers, url = _analyst_headers(database)
    assert api_client.post(url, json={"rawInput": "x", "ctr": "non-number"}, headers=headers).status_code == 422
    assert api_client.post(url, json={"rawInput": "x", "unexpected": 1}, headers=headers).status_code == 422
    assert api_client.post(url, json={"rawInput": "x" * 12001}, headers=headers).status_code == 422
    assert provider_calls == []


def test_analysis_api_preserves_frontend_contract(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    headers, url = _analyst_headers(database)
    response = api_client.post(url, json=BASE_PAYLOAD, headers=headers)
    assert response.status_code == 200
    assert set(response.json()) == {"analysis"}
    assert response.json()["analysis"].startswith("## Estado general")
    assert provider_calls[0].campaign == "Test"


def test_missing_key_returns_safe_message(api_client, database, monkeypatch):  # type: ignore[no-untyped-def]
    headers, url = _analyst_headers(database)

    async def unavailable(_):  # type: ignore[no-untyped-def]
        from app.core.config import AIConfigurationError

        raise AIConfigurationError("OPENROUTER_API_KEY")

    monkeypatch.setattr(routes, "request_campaign_analysis", unavailable)
    response = api_client.post(url, json=BASE_PAYLOAD, headers=headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "AI backend unavailable"}


def test_upstream_error_does_not_leak_secret(api_client, database, monkeypatch):  # type: ignore[no-untyped-def]
    headers, url = _analyst_headers(database)

    async def unavailable(_):  # type: ignore[no-untyped-def]
        from app.integrations.openrouter import AIProviderError

        raise AIProviderError("AI_401")

    monkeypatch.setattr(routes, "request_campaign_analysis", unavailable)
    response = api_client.post(url, json=BASE_PAYLOAD, headers=headers)
    assert response.status_code == 502
    assert "AI_401" not in response.text
