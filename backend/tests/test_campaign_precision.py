"""Numeric precision: values are stored exactly as sent, or refused. Nothing is rounded silently.

Column types (see migration 0003_campaigns): investment and revenue numeric(14,2); cpc, cpl numeric(14,4);
frequency and roas numeric(12,4); ctr numeric(9,4). Counts are bigint.
"""

import uuid

from sqlalchemy import text

from app.domain.access import Role
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.conftest import run
from tests.test_campaign_api import bearer, create_campaign, campaigns_url, record_metrics, seed


def stored_text(raw_engine, entry_id: str, column: str) -> str:  # type: ignore[no-untyped-def]
    async def read():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            result = await connection.execute(
                text(f"SELECT {column}::text FROM campaign_metric_entries WHERE id = :id"),
                {"id": uuid.UUID(entry_id)},
            )
            return result.scalar_one()

    return run(read())


def setup_campaign(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.ANALYST, subject="sub-precision", org_slug="cliente-a")
    headers = bearer("sub-precision")
    campaign = create_campaign(api_client, organization_id, headers)
    return organization_id, campaign["id"], headers


def test_money_is_stored_exactly_as_entered(api_client, database, raw_engine):  # type: ignore[no-untyped-def]
    organization_id, campaign_id, headers = setup_campaign(api_client, database)

    response = record_metrics(
        api_client, organization_id, campaign_id, headers, investment=1234567.89, revenue=0.3, cpc=0.1
    )

    assert response.status_code == 201, response.text
    entry_id = response.json()["id"]
    assert stored_text(raw_engine, entry_id, "investment") == "1234567.89"
    assert stored_text(raw_engine, entry_id, "revenue") == "0.30"
    assert stored_text(raw_engine, entry_id, "cpc") == "0.1000"
    assert response.json()["investment"] == 1234567.89


def test_the_largest_value_each_column_can_hold_is_accepted(api_client, database, raw_engine):  # type: ignore[no-untyped-def]
    organization_id, campaign_id, headers = setup_campaign(api_client, database)

    response = record_metrics(
        api_client, organization_id, campaign_id, headers, investment=999999999999.99, roas=99999999.9999
    )

    assert response.status_code == 201, response.text
    assert stored_text(raw_engine, response.json()["id"], "investment") == "999999999999.99"


def test_values_that_would_need_rounding_are_refused_not_rounded(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, campaign_id, headers = setup_campaign(api_client, database)
    refused = [
        {"investment": 10.005},  # third decimal on money
        {"revenue": 0.001},
        {"cpc": 0.12345},  # fifth decimal on a unit cost
        {"frequency": 2.22225},
        {"ctr": 1.23456},
        {"roas": 4.00001},
        {"investment": 1000000000000},  # 13 integer digits: numeric(14,2) holds 12
        {"investment": -0.01},
    ]
    for body in refused:
        response = record_metrics(api_client, organization_id, campaign_id, headers, **body)
        assert response.status_code == 422, body

    history = api_client.get(f"{campaigns_url(organization_id)}/{campaign_id}/metrics", headers=headers)
    assert history.json() == []


def test_fractional_counts_are_refused(api_client, database):  # type: ignore[no-untyped-def]
    organization_id, campaign_id, headers = setup_campaign(api_client, database)
    for field in ("impressions", "reach", "clicks", "leads", "conversions"):
        response = record_metrics(api_client, organization_id, campaign_id, headers, **{field: 1.5})
        assert response.status_code == 422, field
