"""Authentication and organization authorization on the HTTP API, against PostgreSQL."""

import uuid

from cryptography.hazmat.primitives.asymmetric import ec

from app.db.repositories import MembershipRepository, OrganizationRepository, UserRepository
from app.domain.access import Role
from app.main import app
from tests.api_fixtures import api_client, provider_calls, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import OTHER_ISSUER, make_token
from tests.conftest import run

ANALYZE_BODY = {"rawInput": "Campaña: Prueba", "campaign": "Prueba", "investment": 1000, "clicks": 100, "impressions": 2000}


def seed(database, *, role: Role | None, subject: str, org_slug: str = "cliente-a", org_status: str = "active"):  # type: ignore[no-untyped-def]
    """Creates an organization, a user, and optionally their membership. Returns the organization id."""

    async def create():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            organization = await OrganizationRepository(session).create(name=org_slug, slug=org_slug)
            if org_status != "active":
                organization.status = org_status
            user = await UserRepository(session).create(external_subject=subject, email=f"{subject}@example.com")
            if role is not None:
                await MembershipRepository(session).add(organization_id=organization.id, user_id=user.id, role=role)
            return organization.id

    return run(create())


def bearer(subject: str, **kwargs) -> dict:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {make_token(subject, **kwargs)}"}


def analyze_url(organization_id) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/organizations/{organization_id}/campaigns/analyze"


# --- identity endpoint -------------------------------------------------------------------------------

def test_me_requires_a_token(api_client):  # type: ignore[no-untyped-def]
    response = api_client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "AUTHENTICATION_REQUIRED"}
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_me_rejects_expired_token(api_client, database):  # type: ignore[no-untyped-def]
    seed(database, role=Role.ANALYST, subject="sub-expired")
    response = api_client.get("/api/v1/me", headers=bearer("sub-expired", expires_in=-600))
    assert response.status_code == 401
    assert response.json() == {"detail": "INVALID_TOKEN"}


def test_me_rejects_token_from_another_project(api_client):  # type: ignore[no-untyped-def]
    response = api_client.get("/api/v1/me", headers=bearer("sub-other", issuer=OTHER_ISSUER))
    assert response.status_code == 401


def test_me_provisions_new_user_without_any_organization(api_client, database):  # type: ignore[no-untyped-def]
    subject = f"sub-new-{uuid.uuid4()}"
    response = api_client.get("/api/v1/me", headers=bearer(subject, email="nuevo@cliente.com"))
    assert response.status_code == 200
    body = response.json()
    assert body["organizations"] == []
    assert body["user"]["email"] == "nuevo@cliente.com"
    # Minimal output: the provider subject and other internal fields are not returned.
    assert subject not in response.text
    assert set(body["user"]) == {"id", "email"}


def test_me_lists_only_the_organizations_the_user_belongs_to(api_client, database):  # type: ignore[no-untyped-def]
    seed(database, role=Role.ANALYST, subject="sub-member", org_slug="cliente-a")
    seed(database, role=None, subject="sub-unrelated", org_slug="cliente-b")
    response = api_client.get("/api/v1/me", headers=bearer("sub-member"))
    assert response.status_code == 200
    organizations = response.json()["organizations"]
    assert [(o["slug"], o["role"]) for o in organizations] == [("cliente-a", "analyst")]


# --- organization-scoped analysis ----------------------------------------------------------------------

def test_anonymous_analysis_is_refused_and_never_reaches_the_provider(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    organization_id = seed(database, role=Role.OWNER, subject="sub-owner")
    response = api_client.post(analyze_url(organization_id), json=ANALYZE_BODY)
    assert response.status_code == 401
    assert provider_calls == []


def test_user_without_membership_gets_not_found(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    organization_id = seed(database, role=None, subject="sub-outsider")
    response = api_client.post(analyze_url(organization_id), json=ANALYZE_BODY, headers=bearer("sub-outsider"))
    assert response.status_code == 404
    assert provider_calls == []


def test_client_viewer_can_read_but_cannot_generate_paid_analysis(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    organization_id = seed(database, role=Role.CLIENT_VIEWER, subject="sub-viewer")
    response = api_client.post(analyze_url(organization_id), json=ANALYZE_BODY, headers=bearer("sub-viewer"))
    assert response.status_code == 403
    assert response.json() == {"detail": "FORBIDDEN"}
    assert provider_calls == []


def test_analyst_can_analyze(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    organization_id = seed(database, role=Role.ANALYST, subject="sub-analyst")
    response = api_client.post(analyze_url(organization_id), json=ANALYZE_BODY, headers=bearer("sub-analyst"))
    assert response.status_code == 200
    assert response.json()["analysis"].startswith("## Estado general")
    assert len(provider_calls) == 1


def test_owner_can_analyze(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    organization_id = seed(database, role=Role.OWNER, subject="sub-owner-ok")
    response = api_client.post(analyze_url(organization_id), json=ANALYZE_BODY, headers=bearer("sub-owner-ok"))
    assert response.status_code == 200
    assert len(provider_calls) == 1


def test_member_of_one_organization_cannot_analyze_another(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    seed(database, role=Role.OWNER, subject="sub-a-owner", org_slug="cliente-a")
    other = seed(database, role=None, subject="sub-b-owner", org_slug="cliente-b")
    response = api_client.post(analyze_url(other), json=ANALYZE_BODY, headers=bearer("sub-a-owner"))
    assert response.status_code == 404
    assert provider_calls == []


def test_suspended_organization_is_refused(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    organization_id = seed(database, role=Role.OWNER, subject="sub-suspended", org_status="suspended")
    response = api_client.post(analyze_url(organization_id), json=ANALYZE_BODY, headers=bearer("sub-suspended"))
    assert response.status_code == 403
    assert provider_calls == []


def test_forged_token_cannot_reach_analysis(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    organization_id = seed(database, role=Role.OWNER, subject="sub-victim")
    forged = make_token("sub-victim", signing_key=ec.generate_private_key(ec.SECP256R1()))
    response = api_client.post(analyze_url(organization_id), json=ANALYZE_BODY, headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
    assert provider_calls == []


def test_expired_session_cannot_analyze(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    organization_id = seed(database, role=Role.OWNER, subject="sub-expired-owner")
    response = api_client.post(
        analyze_url(organization_id), json=ANALYZE_BODY, headers=bearer("sub-expired-owner", expires_in=-600)
    )
    assert response.status_code == 401
    assert provider_calls == []


def test_anonymous_route_from_phase_one_no_longer_exists(api_client, provider_calls):  # type: ignore[no-untyped-def]
    response = api_client.post("/api/v1/campaigns/analyze", json=ANALYZE_BODY)
    assert response.status_code == 404
    assert provider_calls == []


def test_invalid_organization_identifier_is_a_validation_error(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    seed(database, role=Role.OWNER, subject="sub-badid")
    response = api_client.post(
        "/api/v1/organizations/not-a-uuid/campaigns/analyze", json=ANALYZE_BODY, headers=bearer("sub-badid")
    )
    assert response.status_code == 422
    assert provider_calls == []


def test_responses_never_echo_the_token(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    organization_id = seed(database, role=Role.OWNER, subject="sub-echo")
    token = make_token("sub-echo")
    success = api_client.post(analyze_url(organization_id), json=ANALYZE_BODY, headers={"Authorization": f"Bearer {token}"})
    me = api_client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert token not in success.text and token not in me.text


def test_missing_identity_configuration_fails_closed(api_client, database, provider_calls):  # type: ignore[no-untyped-def]
    organization_id = seed(database, role=Role.OWNER, subject="sub-noconfig")
    app.state.token_verifier = None
    response = api_client.post(analyze_url(organization_id), json=ANALYZE_BODY, headers=bearer("sub-noconfig"))
    assert response.status_code == 503
    assert provider_calls == []


def test_missing_database_fails_closed(api_client, provider_calls):  # type: ignore[no-untyped-def]
    app.state.database = None
    response = api_client.get("/api/v1/me", headers=bearer("sub-nodb"))
    assert response.status_code == 503
    assert response.json() == {"detail": "database not configured"}
