"""Application flows through HTTP, served with the application role (rckt_app).

These are the flows of the business suites (campaigns, metrics, analyses, quotas, provider outcomes, abandonment, reports,
audit) run against the database role the application really uses. Every request goes through rckt_app. The privileged
connection only seeds the organizations and memberships, and reads the result rows for assertions. The provider is the
simulated transport of test_analysis_api; no call reaches OpenRouter.

Where a flow needs a state that only the owner can create (a lease that has expired), the owner moves it and the
application then performs the operation under test. Nothing here grants rckt_app more privilege.
"""

import uuid

import httpx
import pytest
from sqlalchemy import text

from app.db import analysis_ledger as ledger
from app.db.session import identity_scope
from app.domain.access import Role
from tests.api_fixtures import token_verifier  # noqa: F401  (pytest fixture)
from tests.test_analysis_api import BODY, INVALID, VALID, headers, ok, post, provider  # noqa: F401  (pytest fixture)
from tests.test_analysis_ledger import PRIMARY, REPORT
from tests.test_campaign_api import CAMPAIGN_BODY, METRIC_BODY, add_member, seed
from tests.test_db_security import app_client, app_database, app_url, token_for, raw_run  # noqa: F401  (pytest fixtures)
from tests.test_quota import set_quota
from tests.conftest import run

ANALYSIS_HASH = "f" * 64


def _campaigns(org) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/organizations/{org}/campaigns"


def _count(raw_engine, sql: str, params: dict) -> int:  # type: ignore[no-untyped-def]
    async def read():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            return (await connection.execute(text(sql), params)).scalar_one()

    return run(read())


def _owner_org(database, slug: str, subject: str):  # type: ignore[no-untyped-def]
    org, _ = seed(database, role=Role.OWNER, subject=subject, org_slug=slug)
    return org


# --- identity and memberships -------------------------------------------------------------------------------------------

def test_a_first_sign_in_creates_the_identity_without_any_organization(app_client):  # type: ignore[no-untyped-def]
    response = app_client.get("/api/v1/me", headers=token_for("integration-first-sign-in"))

    assert response.status_code == 200
    assert response.json()["organizations"] == []


# --- campaigns, metrics and history -------------------------------------------------------------------------------------

def test_the_owner_edits_records_metrics_archives_and_reads_the_history(app_client, database):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-camp-a", "int-owner-camp")
    headers_owner = token_for("int-owner-camp")
    created = app_client.post(_campaigns(org), json=CAMPAIGN_BODY, headers=headers_owner)
    assert created.status_code == 201
    campaign_id = created.json()["id"]
    base = f"{_campaigns(org)}/{campaign_id}"

    edited = app_client.patch(base, json={"name": "Campaña Editada"}, headers=headers_owner)
    recorded = app_client.post(f"{base}/metrics", json=METRIC_BODY, headers=headers_owner)
    history = app_client.get(f"{base}/metrics", headers=headers_owner)
    archived = app_client.post(f"{base}/archive", headers=headers_owner)
    listing = app_client.get(_campaigns(org), headers=headers_owner)

    assert edited.status_code == 200 and edited.json()["name"] == "Campaña Editada"
    assert recorded.status_code == 201
    assert [m["id"] for m in history.json()] == [recorded.json()["id"]]
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None
    assert campaign_id not in [c["id"] for c in listing.json()], "archived campaigns are hidden by default"


def test_the_analyst_creates_and_records_but_cannot_edit_or_archive(app_client, database):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-camp-b", "int-owner-b")
    add_member(database, organization_id=org, subject="int-analyst-b", role=Role.ANALYST)
    analyst = token_for("int-analyst-b")
    created = app_client.post(_campaigns(org), json=CAMPAIGN_BODY, headers=analyst)
    assert created.status_code == 201
    base = f"{_campaigns(org)}/{created.json()['id']}"

    assert app_client.post(f"{base}/metrics", json=METRIC_BODY, headers=analyst).status_code == 201
    assert app_client.patch(base, json={"name": "x"}, headers=analyst).status_code == 403
    assert app_client.post(f"{base}/archive", headers=analyst).status_code == 403


def test_the_client_viewer_reads_history_and_records_nothing(app_client, database):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-camp-c", "int-owner-c")
    add_member(database, organization_id=org, subject="int-viewer-c", role=Role.CLIENT_VIEWER)
    created = app_client.post(_campaigns(org), json=CAMPAIGN_BODY, headers=token_for("int-owner-c"))
    base = f"{_campaigns(org)}/{created.json()['id']}"

    assert app_client.get(base, headers=token_for("int-viewer-c")).status_code == 200
    assert app_client.post(f"{base}/metrics", json=METRIC_BODY, headers=token_for("int-viewer-c")).status_code == 403
    assert app_client.post(_campaigns(org), json=CAMPAIGN_BODY, headers=token_for("int-viewer-c")).status_code == 403


def test_a_combined_creation_with_invalid_metrics_stores_neither_the_campaign_nor_the_entry(app_client, database, raw_engine):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-camp-d", "int-owner-d")
    response = app_client.post(f"{_campaigns(org)}/with-metrics",
                               json={"campaign": CAMPAIGN_BODY, "metrics": {**METRIC_BODY, "investment": -1}},
                               headers=token_for("int-owner-d"))

    assert response.status_code == 422
    assert _count(raw_engine, "SELECT count(*) FROM public.campaigns WHERE organization_id = :o", {"o": org}) == 0
    assert _count(raw_engine, "SELECT count(*) FROM public.campaign_metric_entries WHERE organization_id = :o", {"o": org}) == 0


def test_a_combined_creation_stores_the_campaign_and_its_first_entry_together(app_client, database, raw_engine):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-camp-e", "int-owner-e")
    response = app_client.post(f"{_campaigns(org)}/with-metrics",
                               json={"campaign": CAMPAIGN_BODY, "metrics": METRIC_BODY}, headers=token_for("int-owner-e"))

    assert response.status_code == 201
    assert _count(raw_engine, "SELECT count(*) FROM public.campaign_metric_entries WHERE campaign_id = :c",
                  {"c": uuid.UUID(response.json()["campaign"]["id"])}) == 1


def test_an_outsider_and_another_organization_get_not_found_on_campaigns(app_client, database):  # type: ignore[no-untyped-def]
    org_a = _owner_org(database, "int-camp-f", "int-owner-f")
    org_b = _owner_org(database, "int-camp-g", "int-owner-g")
    campaign = app_client.post(_campaigns(org_b), json=CAMPAIGN_BODY, headers=token_for("int-owner-g")).json()

    assert app_client.get(_campaigns(org_b), headers=token_for("int-owner-f")).status_code == 404
    assert app_client.get(f"{_campaigns(org_a)}/{campaign['id']}", headers=token_for("int-owner-f")).status_code == 404
    assert app_client.get(_campaigns(org_b), headers=token_for("int-nobody")).status_code == 404


# --- analyses: creation, replay, quotas, provider outcomes -------------------------------------------------------------

def test_an_analysis_with_quota_disabled_succeeds_and_is_counted(app_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-an-a", "int-owner-an-a")
    provider.replies = [ok(VALID)]

    response = post(app_client, org, "int-owner-an-a", "key-int-disabled-01")

    assert response.status_code == 201 and response.json()["status"] == "succeeded"
    assert _count(raw_engine, "SELECT count(*) FROM public.analyses WHERE organization_id = :o", {"o": org}) == 1


def test_a_provider_timeout_falls_back_and_both_attempts_are_recorded(app_client, database, provider):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-an-b", "int-owner-an-b")
    provider.replies = [httpx.ReadTimeout("slow"), ok(VALID)]

    body = post(app_client, org, "int-owner-an-b", "key-int-fallback-1").json()

    assert body["status"] == "succeeded" and body["used_fallback"] is True
    assert [(a["model_role"], a["status"], a["error_code"]) for a in body["attempts"]] == [
        ("primary", "failed", "AI_TIMEOUT"), ("fallback", "succeeded", None)]


def test_a_format_retry_is_a_second_recorded_step(app_client, database, provider):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-an-c", "int-owner-an-c")
    provider.replies = [ok(INVALID), ok(VALID)]

    body = post(app_client, org, "int-owner-an-c", "key-int-retry-001").json()

    assert body["status"] == "succeeded"
    assert [a["step"] for a in body["attempts"]] == ["initial", "format_retry"]


def test_a_refused_provider_keeps_the_failed_analysis_and_its_unit(app_client, database, provider, raw_engine, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "true")
    org = _owner_org(database, "int-an-d", "int-owner-an-d")
    set_quota(database, org, limit=3)
    provider.replies = [httpx.Response(401), httpx.Response(401)]

    body = post(app_client, org, "int-owner-an-d", "key-int-refused-1").json()

    assert body["status"] == "failed"
    consumed = _count(raw_engine, "SELECT consumed FROM public.analysis_quota_usage WHERE organization_id = :o"
                                  " ORDER BY period_start DESC LIMIT 1", {"o": org})
    assert consumed == 1, "a provider call was reserved, so the unit is kept"


def test_an_exhausted_quota_refuses_before_any_call(app_client, database, provider, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "true")
    org = _owner_org(database, "int-an-e", "int-owner-an-e")
    set_quota(database, org, limit=0)

    response = post(app_client, org, "int-owner-an-e", "key-int-exhaust-1")

    assert response.status_code == 429 and provider.calls == 0


def test_a_campaign_of_another_organization_is_refused_before_any_call(app_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org_a = _owner_org(database, "int-an-f", "int-owner-an-f")
    org_b = _owner_org(database, "int-an-g", "int-owner-an-g")
    campaign = app_client.post(_campaigns(org_b), json=CAMPAIGN_BODY, headers=token_for("int-owner-an-g")).json()

    response = app_client.post(f"/api/v1/organizations/{org_a}/analyses",
                               json={**BODY, "campaign_id": campaign["id"]},
                               headers=headers("int-owner-an-f", "key-int-foreign-1"))

    assert response.status_code >= 400 and provider.calls == 0
    assert _count(raw_engine, "SELECT count(*) FROM public.analyses WHERE organization_id = :o", {"o": org_a}) == 0


def test_an_abandoned_lease_is_closed_through_the_api_without_a_call(app_client, database, app_database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-an-h", "int-owner-an-h")
    user = _user_id(database, "int-owner-an-h")
    from app.domain.schemas import CampaignInput
    from app.services import analysis_history

    data = CampaignInput(**BODY["data"])

    async def claim_and_reserve():  # type: ignore[no-untyped-def]
        with identity_scope("int-owner-an-h"):
            claim = await ledger.claim_analysis(
                app_database, organization_id=org, requested_by=user, campaign_id=None, metric_entry_id=None,
                idempotency_key="key-int-dead-0001", input_hash=analysis_history.fingerprint(data, None),
                prompt_version=analysis_history.PROMPT_VERSION, model_requested=PRIMARY,
                deterministic_report=analysis_history.deterministic_report(data), lease_seconds=60,
            )
            await ledger.reserve_attempt(app_database, organization_id=org, analysis_id=claim.analysis_id,
                                         attempt_number=1, step="initial", model_role="primary", model=PRIMARY)
            return claim

    claim = run(claim_and_reserve())
    raw_run(raw_engine, f"UPDATE public.analyses SET lease_expires_at = now() - interval '1 second'"
                        f" WHERE id = '{claim.analysis_id}'")

    body = post(app_client, org, "int-owner-an-h", "key-int-dead-0001").json()

    assert (body["status"], body["error_code"]) == ("failed", "ANALYSIS_ABANDONED")
    assert [(a["status"], a["error_code"]) for a in body["attempts"]] == [("unknown", "ANALYSIS_ABANDONED")]
    assert provider.calls == 0


def _user_id(database, subject: str):  # type: ignore[no-untyped-def]
    async def read():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            from sqlalchemy import select
            from app.db.models import AppUser

            return (await session.execute(select(AppUser.id).where(AppUser.external_subject == subject))).scalar_one()

    return run(read())


# --- reports, audit and invariants ---------------------------------------------------------------------------------------

def test_the_usage_report_counts_the_month_for_the_owner_and_refuses_the_analyst(app_client, database, provider):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-rep-a", "int-owner-rep")
    add_member(database, organization_id=org, subject="int-analyst-rep", role=Role.ANALYST)
    provider.replies = [ok(VALID), ok(VALID)]
    assert post(app_client, org, "int-owner-rep", "key-int-report-01").status_code == 201
    assert post(app_client, org, "int-owner-rep", "key-int-report-02", {"data": {**BODY["data"], "investment": 7}}).status_code == 201

    report = app_client.get(f"/api/v1/organizations/{org}/usage", headers=token_for("int-owner-rep"))
    denied = app_client.get(f"/api/v1/organizations/{org}/usage", headers=token_for("int-analyst-rep"))

    assert report.status_code == 200
    assert report.json()["analyses"]["requested"] == 2 and report.json()["analyses"]["succeeded"] == 2
    assert denied.status_code == 403


def test_the_audit_trail_records_created_succeeded_and_failed_with_the_application_login(app_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-aud-a", "int-owner-aud")
    provider.replies = [ok(VALID), httpx.Response(401), httpx.Response(401)]
    assert post(app_client, org, "int-owner-aud", "key-int-aud-0001").status_code == 201
    assert post(app_client, org, "int-owner-aud", "key-int-aud-0002", {"data": {**BODY["data"], "investment": 9}}).json()["status"] == "failed"

    page = app_client.get(f"/api/v1/organizations/{org}/audit-events", headers=token_for("int-owner-aud")).json()

    actions = sorted(item["action"] for item in page["items"])
    assert actions == ["analysis.created", "analysis.created", "analysis.failed", "analysis.succeeded"]
    principals = {row["db_principal"] for row in _rows(raw_engine,
                  "SELECT db_principal FROM public.audit_events WHERE organization_id = :o AND source = 'user'", {"o": org})}
    assert principals == {"rckt_app"}


def test_the_audit_trail_pages_newest_first_without_repeats(app_client, database, provider):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-aud-b", "int-owner-aud-b")
    provider.replies = [ok(VALID)] * 3
    for index in range(3):
        assert post(app_client, org, "int-owner-aud-b", f"key-int-page-{index:03d}",
                    {"data": {**BODY["data"], "investment": 100 + index}}).status_code == 201

    first = app_client.get(f"/api/v1/organizations/{org}/audit-events?limit=4", headers=token_for("int-owner-aud-b")).json()
    second = app_client.get(f"/api/v1/organizations/{org}/audit-events?limit=4&cursor={first['next_cursor']}",
                            headers=token_for("int-owner-aud-b")).json()

    ids = [i["id"] for i in first["items"]] + [i["id"] for i in second["items"]]
    assert len(ids) == 6 == len(set(ids))
    assert ids == sorted(ids, reverse=True)


def test_an_analyst_cannot_read_the_audit_trail(app_client, database):  # type: ignore[no-untyped-def]
    org = _owner_org(database, "int-aud-c", "int-owner-aud-c")
    add_member(database, organization_id=org, subject="int-analyst-aud", role=Role.ANALYST)

    assert app_client.get(f"/api/v1/organizations/{org}/audit-events",
                          headers=token_for("int-analyst-aud")).status_code == 403


def _rows(raw_engine, sql: str, params: dict) -> list[dict]:  # type: ignore[no-untyped-def]
    async def read():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            return [dict(row._mapping) for row in (await connection.execute(text(sql), params)).all()]

    return run(read())
