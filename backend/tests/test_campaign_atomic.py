"""Atomic creation of a campaign with its first metric entry: one request, one transaction, no orphan campaigns."""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.repositories import CampaignMetricRepository
from app.domain.access import Role
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.test_campaign_api import CAMPAIGN_BODY, METRIC_BODY, bearer, campaigns_url, seed


def with_metrics_url(organization_id) -> str:  # type: ignore[no-untyped-def]
    return f"{campaigns_url(organization_id)}/with-metrics"


def with_metrics_body(**metric_overrides) -> dict:  # type: ignore[no-untyped-def]
    return {"campaign": CAMPAIGN_BODY, "metrics": {**METRIC_BODY, **metric_overrides}}


def listed_campaigns(api_client, organization_id, headers) -> list:  # type: ignore[no-untyped-def]
    return api_client.get(f"{campaigns_url(organization_id)}?include_archived=true", headers=headers).json()


def test_one_request_creates_the_campaign_and_its_first_entry(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.ANALYST, subject="sub-atomic-ok", org_slug="cliente-a")
    headers = bearer("sub-atomic-ok")

    response = api_client.post(with_metrics_url(organization_id), json=with_metrics_body(), headers=headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["campaign"]["name"] == CAMPAIGN_BODY["name"]
    assert body["metric_entry"]["campaign_id"] == body["campaign"]["id"]
    assert body["metric_entry"]["investment"] == METRIC_BODY["investment"]

    history = api_client.get(f"{campaigns_url(organization_id)}/{body['campaign']['id']}/metrics", headers=headers)
    assert [entry["id"] for entry in history.json()] == [body["metric_entry"]["id"]]


def test_invalid_metrics_leave_nothing_behind(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-atomic-invalid", org_slug="cliente-a")
    headers = bearer("sub-atomic-invalid")

    response = api_client.post(
        with_metrics_url(organization_id), json=with_metrics_body(impressions=10.5), headers=headers
    )

    assert response.status_code == 422
    assert listed_campaigns(api_client, organization_id, headers) == []


def test_a_failure_while_recording_metrics_leaves_no_orphan_campaign(api_client, database, monkeypatch):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-atomic-fail", org_slug="cliente-a")
    headers = bearer("sub-atomic-fail")

    async def fail(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated failure after the campaign insert")

    monkeypatch.setattr(CampaignMetricRepository, "add", fail)
    with pytest.raises(RuntimeError, match="simulated failure"):
        api_client.post(with_metrics_url(organization_id), json=with_metrics_body(), headers=headers)
    monkeypatch.undo()

    assert listed_campaigns(api_client, organization_id, headers) == []


def test_a_database_rejection_while_recording_metrics_rolls_back_the_campaign(api_client, database, monkeypatch):  # type: ignore[no-untyped-def]
    """The application validation is bypassed on purpose, so PostgreSQL itself must reject the entry (CHECK constraint)."""
    organization_id, _ = seed(database, role=Role.OWNER, subject="sub-atomic-db", org_slug="cliente-a")
    headers = bearer("sub-atomic-db")
    original_add = CampaignMetricRepository.add

    async def add_invalid(self, *, organization_id, campaign_id, created_by, values):  # type: ignore[no-untyped-def]
        return await original_add(
            self,
            organization_id=organization_id,
            campaign_id=campaign_id,
            created_by=created_by,
            values={**values, "investment": Decimal("-1")},
        )

    monkeypatch.setattr(CampaignMetricRepository, "add", add_invalid)
    with pytest.raises(IntegrityError):
        api_client.post(with_metrics_url(organization_id), json=with_metrics_body(), headers=headers)
    monkeypatch.undo()

    assert listed_campaigns(api_client, organization_id, headers) == []


def test_the_atomic_endpoint_applies_the_same_role_rules(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.CLIENT_VIEWER, subject="sub-atomic-viewer", org_slug="cliente-a")
    response = api_client.post(
        with_metrics_url(organization_id), json=with_metrics_body(), headers=bearer("sub-atomic-viewer")
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "FORBIDDEN"}
    assert listed_campaigns(api_client, organization_id, bearer("sub-atomic-viewer")) == []


def test_the_atomic_endpoint_refuses_foreign_organizations(api_client, database):  # type: ignore[no-untyped-def]
    seed(database, role=Role.OWNER, subject="sub-atomic-a", org_slug="cliente-a")
    organization_b, _ = seed(database, role=None, subject="sub-atomic-b", org_slug="cliente-b")
    response = api_client.post(
        with_metrics_url(organization_b), json=with_metrics_body(), headers=bearer("sub-atomic-a")
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "NOT_FOUND"}
