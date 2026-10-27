"""Analysis API and history: idempotency, concurrency, provider failures, roles and organization isolation.

The provider is a simulated transport (httpx.MockTransport). Each test scripts the answers; a call beyond the script
fails the test. No network, no paid calls.
"""

import asyncio
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text

from app.db import analysis_ledger as ledger
from app.domain.access import Role
from app.domain.schemas import AnalysisCreate, CampaignInput
from app.integrations import openrouter
from app.services import analysis, analysis_history
from app.services.analysis import is_parseable
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import make_token
from tests.conftest import run
from tests.test_campaign_api import CAMPAIGN_BODY, METRIC_BODY, add_member, bearer, campaigns_url, seed

VALID = "## Estado general\n🟡 Requiere atención\n## Resumen ejecutivo\nResumen de prueba."
INVALID = "Texto sin el formato obligatorio."
BODY = {
    "data": {
        "rawInput": "Campaña: Prueba\nInversión: 1000\nClics: 100",
        "campaign": "Prueba",
        "investment": 1000,
        "clicks": 100,
    }
}


class Provider:
    def __init__(self) -> None:
        self.replies: list = []
        self.calls = 0


@pytest.fixture
def provider(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENROUTER_API_KEY", "unit-test-secret")
    monkeypatch.setenv("CHAT_PRIMARY_LLM", "primary-test")
    monkeypatch.setenv("CHAT_FALLBACK_LLM", "fallback-test")
    monkeypatch.setenv("CHAT_USE_FALLBACK", "true")
    state = Provider()

    def handler(request: httpx.Request) -> httpx.Response:
        state.calls += 1
        assert state.replies, "the simulated provider was called more times than the test scripted"
        reply = state.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    transport = httpx.MockTransport(handler)
    real = openrouter.run_chat_completion
    monkeypatch.setattr(analysis, "run_chat_completion", lambda messages, **kwargs: real(messages, transport=transport, **kwargs))
    return state


def ok(text_value: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text_value}}]})


def url(organization_id) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/organizations/{organization_id}/analyses"


def headers(subject: str, key: str) -> dict:
    return {"Authorization": f"Bearer {make_token(subject)}", "Idempotency-Key": key}


def post(api_client, organization_id, subject: str, key: str, body: dict | None = None):  # type: ignore[no-untyped-def]
    return api_client.post(url(organization_id), json=body or BODY, headers=headers(subject, key))


def owner(database, slug: str = "cliente-a", subject: str = "owner-a"):  # type: ignore[no-untyped-def]
    organization_id, _ = seed(database, role=Role.OWNER, subject=subject, org_slug=slug)
    return organization_id


def attempts_of(raw_engine, analysis_id: str) -> list[dict]:  # type: ignore[no-untyped-def]
    async def read():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            rows = await connection.execute(
                text("SELECT attempt_number, step, model_role, status, error_code, http_status"
                     " FROM analysis_usage WHERE analysis_id = :id ORDER BY attempt_number"),
                {"id": uuid.UUID(analysis_id)},
            )
            return [dict(row._mapping) for row in rows.all()]

    return run(read())


# --- creation, replay and idempotency -----------------------------------------------------------------------

def test_an_analyst_creates_a_persisted_analysis_with_its_call(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    add_member(database, organization_id=organization_id, subject="analyst-only", role=Role.ANALYST)
    provider.replies = [ok(VALID)]

    response = post(api_client, organization_id, "analyst-only", "key-create-001")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded" and body["format_valid"] is True and body["used_fallback"] is False
    assert body["analysis_markdown"].startswith("## Estado general")
    assert body["campaign_name"] == "Prueba"
    assert body["deterministic_report"]["input_summary"]["campaign"] == "Prueba"
    assert [(a["attempt_number"], a["step"], a["model_role"], a["status"]) for a in body["attempts"]] == [
        (1, "initial", "primary", "succeeded"),
    ]
    assert body["attempts"][0]["usage_reported"] is False and body["attempts"][0]["prompt_tokens"] is None
    assert provider.calls == 1


def test_a_replay_with_the_same_key_returns_the_stored_result_without_a_call(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    provider.replies = [ok(VALID)]
    first = post(api_client, organization_id, "owner-a", "key-replay-001")
    second = post(api_client, organization_id, "owner-a", "key-replay-001")

    assert first.status_code == 201 and second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["id"] == first.json()["id"]
    assert provider.calls == 1


def test_the_same_key_with_different_input_is_refused_without_a_call(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    provider.replies = [ok(VALID)]
    post(api_client, organization_id, "owner-a", "key-reuse-001")
    other = {"data": {**BODY["data"], "investment": 2000}}

    response = post(api_client, organization_id, "owner-a", "key-reuse-001", other)

    assert response.status_code == 422 and response.json() == {"detail": "IDEMPOTENCY_KEY_REUSED"}
    assert provider.calls == 1


@pytest.mark.parametrize(("key", "detail"), [(None, "IDEMPOTENCY_KEY_REQUIRED"), ("short", "IDEMPOTENCY_KEY_INVALID"),
                                              ("has spaces in it", "IDEMPOTENCY_KEY_INVALID")])
def test_missing_or_malformed_keys_are_refused_before_anything_happens(api_client, database, provider, key, detail):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    head = {"Authorization": f"Bearer {make_token('owner-a')}"}
    if key is not None:
        head["Idempotency-Key"] = key
    response = api_client.post(url(organization_id), json=BODY, headers=head)
    assert response.status_code == 400 and response.json() == {"detail": detail}
    assert provider.calls == 0


def test_a_running_analysis_with_a_live_lease_answers_in_progress(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    data = CampaignInput(**BODY["data"])
    run(ledger.claim_analysis(
        database, organization_id=organization_id, requested_by=_user_id(database, "owner-a"), campaign_id=None,
        metric_entry_id=None, idempotency_key="key-busy-001", input_hash=analysis_history.fingerprint(data, None),
        prompt_version=analysis_history.PROMPT_VERSION, model_requested="primary-test",
        deterministic_report=analysis_history.deterministic_report(data),
        lease_seconds=analysis_history.LEASE_SECONDS,
    ))

    response = post(api_client, organization_id, "owner-a", "key-busy-001")

    assert response.status_code == 409 and response.json() == {"detail": "ANALYSIS_IN_PROGRESS"}
    assert provider.calls == 0


def _user_id(database, subject: str):  # type: ignore[no-untyped-def]
    from app.db.repositories import UserRepository

    async def read():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            return (await UserRepository(session).get_by_subject(subject)).id

    return run(read())


def test_an_expired_lease_is_abandoned_and_never_called_again(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    data = CampaignInput(**BODY["data"])
    user = _user_id(database, "owner-a")
    claim = run(ledger.claim_analysis(
        database, organization_id=organization_id, requested_by=user, campaign_id=None, metric_entry_id=None,
        idempotency_key="key-dead-001", input_hash=analysis_history.fingerprint(data, None),
        prompt_version=analysis_history.PROMPT_VERSION, model_requested="primary-test",
        deterministic_report=analysis_history.deterministic_report(data), lease_seconds=60,
    ))
    run(ledger.reserve_attempt(database, organization_id=organization_id, analysis_id=claim.analysis_id,
                               attempt_number=1, step="initial", model_role="primary", model="primary-test"))
    run(_sql(raw_engine, "UPDATE analyses SET lease_expires_at = now() - interval '1 second' WHERE id = :id",
             {"id": claim.analysis_id}))

    response = post(api_client, organization_id, "owner-a", "key-dead-001")

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["error_code"]) == ("failed", "ANALYSIS_ABANDONED")
    assert [(a["status"], a["error_code"]) for a in body["attempts"]] == [("unknown", "ANALYSIS_ABANDONED")]
    assert provider.calls == 0


async def _sql(raw_engine, sql: str, params: dict) -> None:  # type: ignore[no-untyped-def]
    async with raw_engine.begin() as connection:
        await connection.execute(text(sql), params)


# --- provider outcomes, all recorded -----------------------------------------------------------------------------

def test_a_timeout_on_the_primary_model_is_recorded_before_the_fallback(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    provider.replies = [httpx.ReadTimeout("slow"), ok(VALID)]

    body = post(api_client, organization_id, "owner-a", "key-fallback-001").json()

    assert body["status"] == "succeeded" and body["used_fallback"] is True and body["model_used"] == "fallback-test"
    assert [(a["model_role"], a["status"], a["error_code"]) for a in body["attempts"]] == [
        ("primary", "failed", "AI_TIMEOUT"),
        ("fallback", "succeeded", None),
    ]
    assert provider.calls == 2


def test_a_format_retry_is_recorded_as_a_second_step(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    provider.replies = [ok(INVALID), ok(VALID)]

    body = post(api_client, organization_id, "owner-a", "key-retry-001").json()

    assert body["status"] == "succeeded" and body["format_valid"] is True
    assert [(a["step"], a["model_role"]) for a in body["attempts"]] == [("initial", "primary"), ("format_retry", "primary")]


def test_an_invalid_format_after_the_retry_is_stored_as_a_safe_failure(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    provider.replies = [ok(INVALID), ok(INVALID)]

    body = post(api_client, organization_id, "owner-a", "key-invalid-001").json()

    assert (body["status"], body["error_code"]) == ("failed", "AI_INVALID_RESPONSE")
    assert body["analysis_markdown"] is None and body["format_valid"] is None
    assert len(body["attempts"]) == 2 and provider.calls == 2


def test_provider_failures_keep_status_codes_and_never_the_provider_body(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    provider.replies = [httpx.Response(429, text="SECRET provider body"), httpx.Response(500, text="SECRET again")]

    response = post(api_client, organization_id, "owner-a", "key-http-001")

    body = response.json()
    assert (body["status"], body["error_code"]) == ("failed", "AI_HTTP_ERROR")
    assert [(a["error_code"], a["http_status"]) for a in body["attempts"]] == [("AI_HTTP_ERROR", 429), ("AI_HTTP_ERROR", 500)]
    assert "SECRET" not in response.text


def test_missing_provider_configuration_is_stored_without_any_call(api_client, database, provider, monkeypatch):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    monkeypatch.delenv("OPENROUTER_API_KEY")

    body = post(api_client, organization_id, "owner-a", "key-noconfig-001").json()

    assert (body["status"], body["error_code"]) == ("failed", "AI_NOT_CONFIGURED")
    assert body["attempts"] == [] and provider.calls == 0


# --- concurrency (service level, real database) -------------------------------------------------------------------------

def test_two_simultaneous_creations_with_one_key_call_the_provider_once(database, monkeypatch):  # type: ignore[no-untyped-def]
    organization_id = owner(database, slug="cliente-carrera", subject="owner-carrera")
    user = _user_id(database, "owner-carrera")
    calls = []

    async def slow_provider(data, recorder=None):  # type: ignore[no-untyped-def]
        # Records its call like the real flow does, so the service can close the analysis as succeeded.
        calls.append(1)
        observer = recorder.for_step("initial")
        token = await observer.started("primary", "primary-test")
        await asyncio.sleep(0.3)
        await observer.finished(token, error_code=None, http_status=None)
        return VALID

    monkeypatch.setattr(analysis_history, "request_campaign_analysis", slow_provider)
    payload = AnalysisCreate(**BODY)

    async def race():  # type: ignore[no-untyped-def]
        return await asyncio.gather(
            analysis_history.create_analysis(database, organization_id=organization_id, user_id=user,
                                             payload=payload, idempotency_key="key-race-api-001", quota_enforced=False),
            analysis_history.create_analysis(database, organization_id=organization_id, user_id=user,
                                             payload=payload, idempotency_key="key-race-api-001", quota_enforced=False),
            return_exceptions=True,
        )

    results = run(race())
    winners = [r for r in results if isinstance(r, analysis_history.CreateResult) and not r.replayed]
    in_progress = [r for r in results if isinstance(r, analysis_history.AnalysisInProgressError)]
    assert len(winners) == 1 and len(in_progress) == 1
    assert len(calls) == 1


# --- roles and organization isolation -------------------------------------------------------------------------------

def test_a_client_viewer_reads_history_but_never_starts_a_paid_analysis(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    add_member(database, organization_id=organization_id, subject="viewer-a", role=Role.CLIENT_VIEWER)
    provider.replies = [ok(VALID)]
    post(api_client, organization_id, "owner-a", "key-viewer-001")

    refused = post(api_client, organization_id, "viewer-a", "key-viewer-002")
    listed = api_client.get(url(organization_id), headers={"Authorization": f"Bearer {make_token('viewer-a')}"})

    assert refused.status_code == 403 and refused.json() == {"detail": "FORBIDDEN"}
    assert listed.status_code == 200 and len(listed.json()["items"]) == 1
    assert provider.calls == 1


def test_an_outsider_cannot_create_or_read(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    seed(database, role=None, subject="outsider", org_slug="cliente-z")
    assert post(api_client, organization_id, "outsider", "key-outsider-001").status_code == 404
    assert api_client.get(url(organization_id), headers={"Authorization": f"Bearer {make_token('outsider')}"}).status_code == 404
    assert provider.calls == 0


def test_another_organizations_analysis_is_not_found(api_client, database, provider):  # type: ignore[no-untyped-def]
    org_a = owner(database, slug="cliente-a", subject="owner-a")
    org_b = owner(database, slug="cliente-b", subject="owner-b")
    provider.replies = [ok(VALID)]
    created = post(api_client, org_b, "owner-b", "key-isolated-001").json()

    response = api_client.get(f"{url(org_a)}/{created['id']}", headers={"Authorization": f"Bearer {make_token('owner-a')}"})

    assert response.status_code == 404 and response.json() == {"detail": "NOT_FOUND"}


def test_a_campaign_from_another_organization_is_refused_before_any_call(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org_a = owner(database, slug="cliente-a", subject="owner-a")
    org_b = owner(database, slug="cliente-b", subject="owner-b")
    campaign = uuid.uuid4()
    run(_sql(raw_engine, "INSERT INTO campaigns (id, organization_id, name) VALUES (:id, :org, 'Ajena')",
             {"id": campaign, "org": org_b}))

    response = post(api_client, org_a, "owner-a", "key-campaign-001", {**BODY, "campaign_id": str(campaign)})

    assert response.status_code == 404 and provider.calls == 0


# --- history -------------------------------------------------------------------------------------------------------------

def test_history_is_paginated_newest_first_and_omits_report_text(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    provider.replies = [ok(VALID), ok(VALID), ok(VALID)]
    created = [post(api_client, organization_id, "owner-a", f"key-page-00{i}").json()["id"] for i in range(3)]
    head = {"Authorization": f"Bearer {make_token('owner-a')}"}

    first = api_client.get(f"{url(organization_id)}?limit=2", headers=head).json()
    second = api_client.get(f"{url(organization_id)}?limit=2&offset=2", headers=head).json()
    # The order is the recorded created_at, newest first. The server clock can step backwards between transactions,
    # so the expected order is read from the stored timestamps rather than assumed from insertion order.
    stored = {i: api_client.get(f"{url(organization_id)}/{i}", headers=head).json()["created_at"] for i in created}
    newest_first = sorted(created, key=lambda i: stored[i], reverse=True)

    assert [item["id"] for item in first["items"]] == newest_first[:2]
    assert first["has_more"] is True and len(first["items"]) == 2
    assert [item["id"] for item in second["items"]] == newest_first[2:] and second["has_more"] is False
    assert all("analysis_markdown" not in item for item in first["items"])
    assert api_client.get(f"{url(organization_id)}?limit=0", headers=head).status_code == 422


def test_a_single_report_returns_its_text_report_and_attempts(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    provider.replies = [ok(VALID)]
    created = post(api_client, organization_id, "owner-a", "key-detail-001").json()

    detail = api_client.get(f"{url(organization_id)}/{created['id']}",
                            headers={"Authorization": f"Bearer {make_token('owner-a')}"}).json()

    assert detail["analysis_markdown"] == created["analysis_markdown"]
    assert "calculated" in detail["deterministic_report"] and len(detail["attempts"]) == 1


def test_the_deterministic_report_is_plain_json(database):  # type: ignore[no-untyped-def]
    report = analysis_history.deterministic_report(CampaignInput(**BODY["data"]))
    assert json.loads(json.dumps(report))["input_summary"]["campaign"] == "Prueba"
    assert is_parseable(VALID)


def test_an_unexpected_failure_never_leaves_the_analysis_running(api_client, database, provider, monkeypatch):  # type: ignore[no-untyped-def]
    """A bug outside the provider errors must close the analysis as failed, then surface as a server error."""
    organization_id = owner(database)

    async def broken(data, recorder=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr(analysis_history, "request_campaign_analysis", broken)
    with pytest.raises(RuntimeError, match="unexpected bug"):
        post(api_client, organization_id, "owner-a", "key-bug-001")

    monkeypatch.undo()
    listed = api_client.get(url(organization_id), headers={"Authorization": f"Bearer {make_token('owner-a')}"}).json()
    [item] = listed["items"]
    assert (item["status"], item["error_code"]) == ("failed", "INTERNAL_ERROR")


# --- metrics snapshot and the explicit link to a saved entry ---------------------------------------------------------

LINK_DATA = {
    "rawInput": METRIC_BODY["raw_input"],
    "campaign": CAMPAIGN_BODY["name"],
    "channel": "Meta",
    "objective": "Leads",
    "period": "Octubre 2026",
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


def saved_entry(api_client, organization_id, subject: str) -> dict:  # type: ignore[no-untyped-def]
    """Creates a campaign and its first entry through the atomic endpoint, as the interface does on save."""
    response = api_client.post(
        f"{campaigns_url(organization_id)}/with-metrics",
        json={"campaign": CAMPAIGN_BODY, "metrics": METRIC_BODY},
        headers=bearer(subject),
    )
    assert response.status_code == 201, response.text
    return response.json()


def linked_body(saved: dict, data: dict = LINK_DATA) -> dict:  # type: ignore[no-untyped-def]
    return {"campaign_id": saved["campaign"]["id"], "metric_entry_id": saved["metric_entry"]["id"], "data": data}


def test_a_linked_analysis_is_verified_and_stored_with_its_campaign(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    saved = saved_entry(api_client, organization_id, "owner-a")
    provider.replies = [ok(VALID)]

    response = post(api_client, organization_id, "owner-a", "key-link-001", linked_body(saved))

    assert response.status_code == 201, response.text
    assert response.json()["campaign_id"] == saved["campaign"]["id"]
    assert provider.calls == 1


def test_a_changed_input_is_refused_before_anything_is_claimed(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    saved = saved_entry(api_client, organization_id, "owner-a")
    changed = {**LINK_DATA, "investment": 999.0}

    response = post(api_client, organization_id, "owner-a", "key-changed-001", linked_body(saved, changed))

    assert response.status_code == 422 and response.json() == {"detail": "ENTRY_DOES_NOT_MATCH"}
    assert provider.calls == 0
    assert api_client.get(url(organization_id), headers={"Authorization": f"Bearer {make_token('owner-a')}"}).json()["items"] == []


def test_an_entry_of_another_organization_is_not_found(api_client, database, provider):  # type: ignore[no-untyped-def]
    org_a = owner(database, slug="cliente-a", subject="owner-a")
    org_b = owner(database, slug="cliente-b", subject="owner-b")
    foreign = saved_entry(api_client, org_b, "owner-b")

    response = post(api_client, org_a, "owner-a", "key-foreign-entry", linked_body(foreign))

    assert response.status_code == 404 and provider.calls == 0


def test_an_entry_of_another_campaign_is_not_found(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    first = saved_entry(api_client, organization_id, "owner-a")
    second = saved_entry(api_client, organization_id, "owner-a")
    mismatched = {"campaign_id": second["campaign"]["id"], "metric_entry_id": first["metric_entry"]["id"], "data": LINK_DATA}

    response = post(api_client, organization_id, "owner-a", "key-other-campaign", mismatched)

    assert response.status_code == 404 and provider.calls == 0


def test_an_entry_without_its_campaign_is_a_validation_error(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    saved = saved_entry(api_client, organization_id, "owner-a")

    response = post(api_client, organization_id, "owner-a", "key-entry-only",
                    {"metric_entry_id": saved["metric_entry"]["id"], "data": LINK_DATA})

    assert response.status_code == 422 and provider.calls == 0


def test_a_linked_replay_returns_the_stored_analysis(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    saved = saved_entry(api_client, organization_id, "owner-a")
    provider.replies = [ok(VALID)]
    first = post(api_client, organization_id, "owner-a", "key-link-replay", linked_body(saved))
    second = post(api_client, organization_id, "owner-a", "key-link-replay", linked_body(saved))

    assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
    assert provider.calls == 1


def test_the_snapshot_keeps_the_metrics_and_never_the_text(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    provider.replies = [ok(VALID)]
    body = {"data": {"rawInput": "SECRET-RAW-TEXT", "additionalContext": "SECRET-CONTEXT", "campaign": "Prueba",
                     "investment": 1000, "clicks": 100, "previous": {"investment": 800}}}

    created = post(api_client, organization_id, "owner-a", "key-snapshot-001", body).json()

    report = created["deterministic_report"]
    assert report["metrics_snapshot"] == {
        "version": 1,
        "metrics": {"investment": 1000.0, "clicks": 100.0},
        "previous": {"investment": 800.0},
    }
    assert report["calculated"]["cpc"] == 10.0
    assert "SECRET" not in json.dumps(report)


def test_analyses_saved_before_the_snapshot_still_read_without_it(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    user = _user_id(database, "owner-a")
    claim = run(ledger.claim_analysis(
        database, organization_id=organization_id, requested_by=user, campaign_id=None, metric_entry_id=None,
        idempotency_key="key-legacy-001", input_hash="e" * 64, prompt_version="analysis-v1",
        model_requested="primary-test", deterministic_report={"input_summary": {"campaign": "Antigua"}},
        lease_seconds=analysis_history.LEASE_SECONDS,
    ))
    run(ledger.finalize_analysis(database, organization_id=organization_id, analysis_id=claim.analysis_id,
                                 status="failed", error_code="INTERNAL_ERROR"))

    detail = api_client.get(f"{url(organization_id)}/{claim.analysis_id}",
                            headers={"Authorization": f"Bearer {make_token('owner-a')}"})

    assert detail.status_code == 200
    assert "metrics_snapshot" not in detail.json()["deterministic_report"]
    assert detail.json()["campaign_name"] == "Antigua"


# --- what the link certifies, and what it refuses -------------------------------------------------------------------

@pytest.mark.parametrize(
    "changed",
    [
        {"campaign": "Otra campana"},
        {"channel": "Google Ads"},
        {"objective": "Ventas"},
        {"period": "Noviembre 2026"},
        {"additionalContext": "Contexto que el vinculo no puede certificar"},
    ],
)
def test_a_link_is_refused_when_any_certified_or_unverifiable_field_differs(api_client, database, provider, changed):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    saved = saved_entry(api_client, organization_id, "owner-a")

    response = post(api_client, organization_id, "owner-a", "key-field-" + next(iter(changed)),
                    linked_body(saved, {**LINK_DATA, **changed}))

    assert response.status_code == 422 and response.json() == {"detail": "ENTRY_DOES_NOT_MATCH"}
    assert provider.calls == 0


def test_renaming_the_campaign_after_saving_breaks_the_link(api_client, database, provider):  # type: ignore[no-untyped-def]
    organization_id = owner(database)
    saved = saved_entry(api_client, organization_id, "owner-a")
    renamed = api_client.patch(f"{campaigns_url(organization_id)}/{saved['campaign']['id']}",
                               json={"name": "Nombre nuevo"}, headers=bearer("owner-a"))
    assert renamed.status_code == 200

    response = post(api_client, organization_id, "owner-a", "key-renamed", linked_body(saved))

    assert response.status_code == 422 and response.json() == {"detail": "ENTRY_DOES_NOT_MATCH"}
    assert provider.calls == 0


def test_a_retry_with_the_same_key_and_body_replays_even_after_the_campaign_was_saved(api_client, database, provider):  # type: ignore[no-untyped-def]
    """The browser timed out after an unlinked request; the campaign was saved afterwards; the retry repeats the
    original body. It replays the stored analysis and does not call the provider again."""
    organization_id = owner(database)
    provider.replies = [ok(VALID)]
    first = post(api_client, organization_id, "owner-a", "key-retry-timeout")
    assert first.status_code == 201
    saved_entry(api_client, organization_id, "owner-a")

    retry = post(api_client, organization_id, "owner-a", "key-retry-timeout")

    assert retry.status_code == 200 and retry.json()["id"] == first.json()["id"]
    assert provider.calls == 1


def test_a_retry_that_adds_a_link_is_refused_without_a_call(api_client, database, provider):  # type: ignore[no-untyped-def]
    """Adding a link changes the fingerprint, so the same key is refused. Nothing is charged."""
    organization_id = owner(database)
    provider.replies = [ok(VALID)]
    post(api_client, organization_id, "owner-a", "key-retry-linked")
    saved = saved_entry(api_client, organization_id, "owner-a")

    response = post(api_client, organization_id, "owner-a", "key-retry-linked", linked_body(saved))

    assert response.status_code == 422 and response.json() == {"detail": "IDEMPOTENCY_KEY_REUSED"}
    assert provider.calls == 1
