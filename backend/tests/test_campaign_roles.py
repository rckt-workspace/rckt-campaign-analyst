"""Role matrix for campaigns and metric entries. Every role is checked against every operation.

owner: create, read, edit and archive campaigns; record and read metrics.
analyst: create and read campaigns; record and read metrics. Cannot edit or archive.
client_viewer: read campaigns and their metric history only.
"""

import pytest

from app.domain.access import Role
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.test_campaign_api import (
    CAMPAIGN_BODY,
    METRIC_BODY,
    add_member,
    bearer,
    campaigns_url,
    create_campaign,
    record_metrics,
    seed,
)
from tests.test_campaign_atomic import with_metrics_body, with_metrics_url

OPERATIONS = (
    "create",
    "create_with_metrics",
    "list",
    "get",
    "patch",
    "archive",
    "record_metrics",
    "list_metrics",
)

EXPECTED = {
    Role.OWNER: {
        "create": 201,
        "create_with_metrics": 201,
        "list": 200,
        "get": 200,
        "patch": 200,
        "archive": 200,
        "record_metrics": 201,
        "list_metrics": 200,
    },
    Role.ANALYST: {
        "create": 201,
        "create_with_metrics": 201,
        "list": 200,
        "get": 200,
        "patch": 403,
        "archive": 403,
        "record_metrics": 201,
        "list_metrics": 200,
    },
    Role.CLIENT_VIEWER: {
        "create": 403,
        "create_with_metrics": 403,
        "list": 200,
        "get": 200,
        "patch": 403,
        "archive": 403,
        "record_metrics": 403,
        "list_metrics": 200,
    },
}


def perform(api_client, operation: str, organization_id, campaign_id, headers):  # type: ignore[no-untyped-def]
    base = f"{campaigns_url(organization_id)}/{campaign_id}"
    if operation == "create":
        return api_client.post(campaigns_url(organization_id), json=CAMPAIGN_BODY, headers=headers)
    if operation == "create_with_metrics":
        return api_client.post(with_metrics_url(organization_id), json=with_metrics_body(), headers=headers)
    if operation == "list":
        return api_client.get(campaigns_url(organization_id), headers=headers)
    if operation == "get":
        return api_client.get(base, headers=headers)
    if operation == "patch":
        return api_client.patch(base, json={"objective": "Ventas"}, headers=headers)
    if operation == "archive":
        return api_client.post(f"{base}/archive", headers=headers)
    if operation == "record_metrics":
        return api_client.post(f"{base}/metrics", json=METRIC_BODY, headers=headers)
    if operation == "list_metrics":
        return api_client.get(f"{base}/metrics", headers=headers)
    raise AssertionError(f"unknown operation {operation}")


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("role", list(Role))
def test_role_is_allowed_or_refused_as_designed(api_client, database, role, operation):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-matrix-owner", org_slug="cliente-a")
    campaign = create_campaign(api_client, organization_id, bearer("sub-matrix-owner"))
    record_metrics(api_client, organization_id, campaign["id"], bearer("sub-matrix-owner"))

    if role is Role.OWNER:
        headers = bearer("sub-matrix-owner")
    else:
        add_member(database, organization_id=organization_id, subject="sub-matrix-member", role=role)
        headers = bearer("sub-matrix-member")

    response = perform(api_client, operation, organization_id, campaign["id"], headers)

    assert response.status_code == EXPECTED[role][operation], f"{role.value} {operation}: {response.text}"
    if response.status_code == 403:
        assert response.json() == {"detail": "FORBIDDEN"}


# Expected outcome on an archived campaign. Role is checked first: an analyst's edit is 403, not 409.
ARCHIVED_EXPECTED = {
    Role.OWNER: {"patch": 409, "record_metrics": 409, "list_metrics": 200},
    Role.ANALYST: {"patch": 403, "record_metrics": 409, "list_metrics": 200},
    Role.CLIENT_VIEWER: {"patch": 403, "record_metrics": 403, "list_metrics": 200},
}


@pytest.mark.parametrize("operation", ["patch", "record_metrics", "list_metrics"])
@pytest.mark.parametrize("role", list(Role))
def test_archived_campaign_outcome_for_every_role(api_client, database, role, operation):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-matrix-archived", org_slug="cliente-a")
    campaign = create_campaign(api_client, organization_id, bearer("sub-matrix-archived"))
    archived = api_client.post(
        f"{campaigns_url(organization_id)}/{campaign['id']}/archive", headers=bearer("sub-matrix-archived")
    )
    assert archived.status_code == 200

    if role is Role.OWNER:
        headers = bearer("sub-matrix-archived")
    else:
        add_member(database, organization_id=organization_id, subject="sub-matrix-archived-member", role=role)
        headers = bearer("sub-matrix-archived-member")

    response = perform(api_client, operation, organization_id, campaign["id"], headers)
    assert response.status_code == ARCHIVED_EXPECTED[role][operation], response.text
