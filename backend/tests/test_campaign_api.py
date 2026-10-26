"""Campaign and metric-entry API: roles, tenant isolation, validation, archiving and append-only history.

Runs against the real FastAPI app and the PostgreSQL test database. No provider is called.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.repositories import MembershipRepository, OrganizationRepository, UserRepository
from app.domain.access import Role
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import make_token
from tests.conftest import run

CAMPAIGN_BODY = {
    "name": "Campaña Verano",
    "channel": "Meta",
    "objective": "Leads",
    "period_label": "Octubre 2026",
    "currency": "COP",
    "period_start": "2026-10-01",
    "period_end": "2026-10-31",
}

METRIC_BODY = {
    "source": "form",
    "raw_input": "Campaña: Verano\nInversión: 1000.5",
    "period_label": "Octubre 2026",
    "investment": 1000.5,
    "impressions": 20000,
    "reach": 9000,
    "frequency": 2.2222,
    "clicks": 300,
    "ctr": 1.5,
    "cpc": 3.3375,
    "leads": 12,
    "cpl": 83.375,
    "conversions": 4,
    "revenue": 4000,
    "roas": 4,
    "previous": {"investment": 900, "clicks": 250},
}


def seed(database, *, role: Role | None, subject: str, org_slug: str) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    """Creates an organization and a user, with a membership when a role is given. Returns (organization_id, user_id)."""

    async def create():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            organization = await OrganizationRepository(session).create(name=org_slug, slug=org_slug)
            user = await UserRepository(session).create(external_subject=subject, email=f"{subject}@example.com")
            if role is not None:
                await MembershipRepository(session).add(organization_id=organization.id, user_id=user.id, role=role)
            return organization.id, user.id

    return run(create())


def add_member(database, *, organization_id: uuid.UUID, subject: str, role: Role) -> None:  # type: ignore[no-untyped-def]
    """Adds a new user with a membership to an existing organization."""

    async def add():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            user = await UserRepository(session).create(external_subject=subject, email=f"{subject}@example.com")
            await MembershipRepository(session).add(organization_id=organization_id, user_id=user.id, role=role)

    run(add())


def bearer(subject: str) -> dict:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {make_token(subject)}"}


def campaigns_url(organization_id) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/organizations/{organization_id}/campaigns"


def create_campaign(api_client, organization_id, headers, **overrides):  # type: ignore[no-untyped-def]
    response = api_client.post(campaigns_url(organization_id), json={**CAMPAIGN_BODY, **overrides}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def record_metrics(api_client, organization_id, campaign_id, headers, **overrides):  # type: ignore[no-untyped-def]
    response = api_client.post(
        f"{campaigns_url(organization_id)}/{campaign_id}/metrics", json={**METRIC_BODY, **overrides}, headers=headers
    )
    return response


# --- creation and reading ---------------------------------------------------------------------------------

def test_owner_creates_campaign_in_the_organization_of_the_path(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-owner-create", org_slug="cliente-a")
    campaign = create_campaign(api_client, organization_id, bearer("sub-owner-create"))

    assert campaign["name"] == "Campaña Verano"
    assert campaign["currency"] == "COP"
    assert campaign["archived_at"] is None
    assert campaign["created_at"] and campaign["updated_at"]
    assert "organization_id" not in campaign and "created_by" not in campaign


def test_analyst_can_create_and_read_campaigns(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.ANALYST, subject="sub-analyst-create", org_slug="cliente-a")
    headers = bearer("sub-analyst-create")
    campaign = create_campaign(api_client, organization_id, headers)

    listed = api_client.get(campaigns_url(organization_id), headers=headers)
    single = api_client.get(f"{campaigns_url(organization_id)}/{campaign['id']}", headers=headers)
    assert listed.status_code == 200 and [c["id"] for c in listed.json()] == [campaign["id"]]
    assert single.status_code == 200 and single.json()["name"] == "Campaña Verano"


def test_client_viewer_can_read_but_never_create(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.CLIENT_VIEWER, subject="sub-viewer-read", org_slug="cliente-a")
    headers = bearer("sub-viewer-read")
    response = api_client.post(campaigns_url(organization_id), json=CAMPAIGN_BODY, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "FORBIDDEN"}
    assert api_client.get(campaigns_url(organization_id), headers=headers).status_code == 200


def test_organization_id_in_the_body_is_refused(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-owner-body", org_slug="cliente-a")
    other_org, _ = seed(database, role=None, subject="sub-other-org", org_slug="cliente-b")
    response = api_client.post(
        campaigns_url(organization_id),
        json={**CAMPAIGN_BODY, "organization_id": str(other_org)},
        headers=bearer("sub-owner-body"),
    )
    assert response.status_code == 422


def test_listing_shows_only_the_organization_and_hides_archived_by_default(api_client, database):  # type: ignore[no-untyped-def]
    org_a, _ = seed(database, role=Role.OWNER, subject="sub-list-a", org_slug="cliente-a")
    org_b, _ = seed(database, role=Role.OWNER, subject="sub-list-b", org_slug="cliente-b")
    headers_a = bearer("sub-list-a")
    kept = create_campaign(api_client, org_a, headers_a, name="Activa")
    archived = create_campaign(api_client, org_a, headers_a, name="Archivada")
    create_campaign(api_client, org_b, bearer("sub-list-b"), name="De otro cliente")
    api_client.post(f"{campaigns_url(org_a)}/{archived['id']}/archive", headers=headers_a)

    active_only = api_client.get(campaigns_url(org_a), headers=headers_a).json()
    with_archived = api_client.get(f"{campaigns_url(org_a)}?include_archived=true", headers=headers_a).json()
    assert [c["name"] for c in active_only] == ["Activa"]
    assert {c["id"] for c in with_archived} == {kept["id"], archived["id"]}
    assert all(c["name"] != "De otro cliente" for c in with_archived)


def test_pagination_limits_are_enforced(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-page", org_slug="cliente-a")
    headers = bearer("sub-page")
    for index in range(3):
        create_campaign(api_client, organization_id, headers, name=f"Campaña {index}")

    page = api_client.get(f"{campaigns_url(organization_id)}?limit=2&offset=1", headers=headers).json()
    assert len(page) == 2
    assert api_client.get(f"{campaigns_url(organization_id)}?limit=0", headers=headers).status_code == 422
    assert api_client.get(f"{campaigns_url(organization_id)}?limit=201", headers=headers).status_code == 422


# --- cross-organization access -------------------------------------------------------------------------------

def test_an_owner_of_one_organization_cannot_touch_another_organizations_campaign(api_client, database):  # type: ignore[no-untyped-def]
    org_a, _ = seed(database, role=Role.OWNER, subject="sub-iso-a", org_slug="cliente-a")
    org_b, _ = seed(database, role=Role.OWNER, subject="sub-iso-b", org_slug="cliente-b")
    campaign_b = create_campaign(api_client, org_b, bearer("sub-iso-b"))
    headers_a = bearer("sub-iso-a")
    base_b = f"{campaigns_url(org_b)}/{campaign_b['id']}"

    # Owner A uses its own organization in the path, with a campaign id that belongs to B.
    own_path = f"{campaigns_url(org_a)}/{campaign_b['id']}"
    assert api_client.get(own_path, headers=headers_a).json() == {"detail": "NOT_FOUND"}
    assert api_client.patch(own_path, json={"name": "Robada"}, headers=headers_a).status_code == 404
    assert api_client.post(f"{own_path}/archive", headers=headers_a).status_code == 404
    assert api_client.post(f"{own_path}/metrics", json=METRIC_BODY, headers=headers_a).status_code == 404
    assert api_client.get(f"{own_path}/metrics", headers=headers_a).status_code == 404

    # Owner A tries to use B's organization in the path: not a member, so the organization is not revealed.
    assert api_client.get(base_b, headers=headers_a).status_code == 404
    assert api_client.get(campaigns_url(org_b), headers=headers_a).status_code == 404

    # Nothing changed for B.
    untouched = api_client.get(base_b, headers=bearer("sub-iso-b")).json()
    assert untouched["name"] == "Campaña Verano" and untouched["archived_at"] is None


def test_non_member_gets_not_found_on_every_campaign_endpoint(api_client, database):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="sub-member-only", org_slug="cliente-a")
    campaign = create_campaign(api_client, org, bearer("sub-member-only"))
    seed(database, role=None, subject="sub-outsider", org_slug="cliente-z")
    headers = bearer("sub-outsider")
    base = f"{campaigns_url(org)}/{campaign['id']}"

    responses = [
        api_client.get(campaigns_url(org), headers=headers),
        api_client.post(campaigns_url(org), json=CAMPAIGN_BODY, headers=headers),
        api_client.get(base, headers=headers),
        api_client.patch(base, json={"name": "X"}, headers=headers),
        api_client.post(f"{base}/archive", headers=headers),
        api_client.post(f"{base}/metrics", json=METRIC_BODY, headers=headers),
        api_client.get(f"{base}/metrics", headers=headers),
    ]
    assert [r.status_code for r in responses] == [404] * 7


# --- roles -------------------------------------------------------------------------------------------------------

def test_analyst_cannot_edit_or_archive_campaigns(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.ANALYST, subject="sub-analyst-limits", org_slug="cliente-a")
    campaign = create_campaign(api_client, organization_id, bearer("sub-analyst-limits"))
    base = f"{campaigns_url(organization_id)}/{campaign['id']}"
    headers = bearer("sub-analyst-limits")

    assert api_client.patch(base, json={"name": "Nuevo"}, headers=headers).status_code == 403
    assert api_client.post(f"{base}/archive", headers=headers).status_code == 403
    assert api_client.get(base, headers=headers).json()["name"] == "Campaña Verano"


def test_client_viewer_is_read_only_on_campaigns_and_metrics(api_client, database):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject="sub-owner-ro", org_slug="cliente-a")
    campaign = create_campaign(api_client, org, bearer("sub-owner-ro"))
    assert record_metrics(api_client, org, campaign["id"], bearer("sub-owner-ro")).status_code == 201
    add_member(database, organization_id=org, subject="sub-viewer-ro", role=Role.CLIENT_VIEWER)
    headers = bearer("sub-viewer-ro")
    base = f"{campaigns_url(org)}/{campaign['id']}"

    assert api_client.get(base, headers=headers).status_code == 200
    assert api_client.get(f"{base}/metrics", headers=headers).status_code == 200
    assert api_client.patch(base, json={"name": "X"}, headers=headers).status_code == 403
    assert api_client.post(f"{base}/archive", headers=headers).status_code == 403
    assert record_metrics(api_client, org, campaign["id"], headers).status_code == 403


# --- editing and archiving ---------------------------------------------------------------------------------------

def test_owner_edits_only_the_fields_that_are_sent(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-owner-edit", org_slug="cliente-a")
    headers = bearer("sub-owner-edit")
    campaign = create_campaign(api_client, organization_id, headers)

    response = api_client.patch(
        f"{campaigns_url(organization_id)}/{campaign['id']}", json={"channel": None, "objective": "Ventas"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["channel"] is None
    assert body["objective"] == "Ventas"
    assert body["name"] == "Campaña Verano"
    assert body["period_start"] == "2026-10-01"
    assert body["updated_at"] != campaign["updated_at"]


def test_edits_with_invalid_values_are_refused(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-owner-invalid", org_slug="cliente-a")
    headers = bearer("sub-owner-invalid")
    campaign = create_campaign(api_client, organization_id, headers)
    base = f"{campaigns_url(organization_id)}/{campaign['id']}"

    for body in (
        {"name": None},
        {"name": "   "},
        {"currency": "cop"},
        {"currency": None},
        {"organization_id": str(uuid.uuid4())},
    ):
        assert api_client.patch(base, json=body, headers=headers).status_code == 422, body

    # The period is checked against the merged result: the new end is before the stored start.
    response = api_client.patch(base, json={"period_end": "2026-09-01"}, headers=headers)
    assert response.status_code == 422
    assert response.json() == {"detail": "INVALID_PERIOD"}
    assert api_client.get(base, headers=headers).json()["period_end"] == "2026-10-31"


def test_creation_with_reversed_period_is_refused(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-owner-period", org_slug="cliente-a")
    response = api_client.post(
        campaigns_url(organization_id),
        json={**CAMPAIGN_BODY, "period_start": "2026-11-01", "period_end": "2026-10-01"},
        headers=bearer("sub-owner-period"),
    )
    assert response.status_code == 422


def test_archive_is_permanent_idempotent_and_keeps_history_readable(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-owner-archive", org_slug="cliente-a")
    headers = bearer("sub-owner-archive")
    campaign = create_campaign(api_client, organization_id, headers)
    base = f"{campaigns_url(organization_id)}/{campaign['id']}"
    assert record_metrics(api_client, organization_id, campaign["id"], headers).status_code == 201

    first = api_client.post(f"{base}/archive", headers=headers)
    second = api_client.post(f"{base}/archive", headers=headers)
    assert first.status_code == 200 and first.json()["archived_at"] is not None
    assert second.status_code == 200 and second.json()["archived_at"] == first.json()["archived_at"]

    assert api_client.patch(base, json={"name": "Cambio"}, headers=headers).json() == {"detail": "CAMPAIGN_ARCHIVED"}
    refused = record_metrics(api_client, organization_id, campaign["id"], headers)
    assert refused.status_code == 409 and refused.json() == {"detail": "CAMPAIGN_ARCHIVED"}

    history = api_client.get(f"{base}/metrics", headers=headers)
    assert history.status_code == 200 and len(history.json()) == 1
    assert api_client.get(base, headers=headers).json()["name"] == "Campaña Verano"

    # There is no route that deletes a campaign: the resource stays addressable.
    assert api_client.delete(base, headers=headers).status_code == 405
    assert api_client.get(base, headers=headers).status_code == 200


# --- metric entries ----------------------------------------------------------------------------------------------

def test_metric_entry_is_stored_as_entered(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.ANALYST, subject="sub-analyst-metrics", org_slug="cliente-a")
    headers = bearer("sub-analyst-metrics")
    campaign = create_campaign(api_client, organization_id, headers)

    response = record_metrics(api_client, organization_id, campaign["id"], headers)
    assert response.status_code == 201
    entry = response.json()
    assert entry["campaign_id"] == campaign["id"]
    assert entry["source"] == "form"
    assert entry["investment"] == 1000.5
    assert entry["impressions"] == 20000
    assert entry["cpc"] == 3.3375
    assert entry["previous_metrics"]["investment"] == 900
    assert entry["previous_metrics"]["reach"] is None
    assert "created_by" not in entry


def test_metric_entries_are_listed_newest_first(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.ANALYST, subject="sub-analyst-order", org_slug="cliente-a")
    headers = bearer("sub-analyst-order")
    campaign = create_campaign(api_client, organization_id, headers)
    record_metrics(api_client, organization_id, campaign["id"], headers, raw_input="primera")
    record_metrics(api_client, organization_id, campaign["id"], headers, raw_input="segunda")

    history = api_client.get(f"{campaigns_url(organization_id)}/{campaign['id']}/metrics", headers=headers).json()
    assert [entry["raw_input"] for entry in history] == ["segunda", "primera"]


def test_values_the_database_cannot_store_are_refused_before_insert(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.ANALYST, subject="sub-analyst-values", org_slug="cliente-a")
    headers = bearer("sub-analyst-values")
    campaign = create_campaign(api_client, organization_id, headers)

    invalid = [
        {"impressions": 10.5},  # bigint: fractional count
        {"clicks": -1},
        {"investment": 1.001},  # numeric(14,2)
        {"cpc": 0.12345},  # numeric(14,4)
        {"source": "email"},
        {"raw_input": "x" * 12001},
        {"period_start": "2026-11-01", "period_end": "2026-10-01"},
        {"organization_id": str(uuid.uuid4())},
        {"previous": {"unknown_metric": 1}},
    ]
    for body in invalid:
        response = api_client.post(
            f"{campaigns_url(organization_id)}/{campaign['id']}/metrics", json={**METRIC_BODY, **body}, headers=headers
        )
        assert response.status_code == 422, body

    assert api_client.get(f"{campaigns_url(organization_id)}/{campaign['id']}/metrics", headers=headers).json() == []


def test_metric_entries_cannot_be_changed_or_deleted_through_the_api(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-owner-history", org_slug="cliente-a")
    headers = bearer("sub-owner-history")
    campaign = create_campaign(api_client, organization_id, headers)
    entry = record_metrics(api_client, organization_id, campaign["id"], headers).json()
    entry_url = f"{campaigns_url(organization_id)}/{campaign['id']}/metrics/{entry['id']}"

    assert api_client.patch(entry_url, json={"investment": 1}, headers=headers).status_code in (404, 405)
    assert api_client.put(entry_url, json=METRIC_BODY, headers=headers).status_code in (404, 405)
    assert api_client.delete(entry_url, headers=headers).status_code in (404, 405)

    history = api_client.get(f"{campaigns_url(organization_id)}/{campaign['id']}/metrics", headers=headers).json()
    assert len(history) == 1 and history[0]["investment"] == 1000.5


def test_database_refuses_direct_changes_to_recorded_history(api_client, database, raw_engine):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-owner-trigger", org_slug="cliente-a")
    headers = bearer("sub-owner-trigger")
    campaign = create_campaign(api_client, organization_id, headers)
    entry = record_metrics(api_client, organization_id, campaign["id"], headers).json()

    async def change(sql: str) -> None:
        async with raw_engine.begin() as connection:
            await connection.execute(text(sql), {"id": uuid.UUID(entry["id"])})

    with pytest.raises(DBAPIError, match="append-only"):
        run(change("UPDATE campaign_metric_entries SET investment = 1 WHERE id = :id"))
    with pytest.raises(DBAPIError, match="append-only"):
        run(change("DELETE FROM campaign_metric_entries WHERE id = :id"))


# --- error responses -----------------------------------------------------------------------------------------------

def test_error_responses_use_stable_codes_and_reveal_nothing_internal(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-owner-errors", org_slug="cliente-a")
    headers = bearer("sub-owner-errors")
    campaign = create_campaign(api_client, organization_id, headers)
    base = f"{campaigns_url(organization_id)}/{campaign['id']}"

    unknown = api_client.get(f"{campaigns_url(organization_id)}/{uuid.uuid4()}", headers=headers)
    assert unknown.status_code == 404 and unknown.json() == {"detail": "NOT_FOUND"}

    anonymous = api_client.get(campaigns_url(organization_id))
    assert anonymous.status_code == 401 and anonymous.json() == {"detail": "AUTHENTICATION_REQUIRED"}

    malformed = api_client.get(f"{campaigns_url(organization_id)}/not-a-uuid", headers=headers)
    assert malformed.status_code == 422

    api_client.post(f"{base}/archive", headers=headers)
    archived = record_metrics(api_client, organization_id, campaign["id"], headers)
    assert archived.status_code == 409 and archived.json() == {"detail": "CAMPAIGN_ARCHIVED"}

    for response in (unknown, anonymous, malformed, archived):
        assert "SQL" not in response.text and "asyncpg" not in response.text and "Traceback" not in response.text
