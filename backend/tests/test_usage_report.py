"""Monthly usage report: who may read it, the exact counts, unknown values, quota state, and the UTC month rules.

Provider calls are simulated (httpx.MockTransport through the analysis test provider). No test reaches OpenRouter.
Semantics: docs/PHASE2_USAGE_REPORT.md.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text

from app.domain.access import Role
from app.domain.usage_report import current_period
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import make_token
from tests.conftest import run
from tests.test_analysis_api import BODY, INVALID, VALID, ok, owner, post, provider  # noqa: F401  (pytest fixtures)
from tests.test_campaign_api import add_member, seed
from tests.test_provider_usage import reply
from tests.test_quota import set_quota
from tests.test_analysis_ledger import PRIMARY, REPORT

HASH = "b" * 64


def _path(org) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/organizations/{org}/usage"


def _report(api_client, org, subject: str, period: str | None = None):  # type: ignore[no-untyped-def]
    params = {} if period is None else {"period": period}
    return api_client.get(_path(org), params=params, headers={"Authorization": f"Bearer {make_token(subject)}"})


def _current() -> str:
    return current_period(datetime.now(UTC)).label


def _exec(raw_engine, sql: str, params: dict | None = None) -> None:  # type: ignore[no-untyped-def]
    async def write():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            await connection.execute(text(sql), params or {})
            await connection.commit()

    run(write())


def _raw_analysis(raw_engine, org, user, *, created, status="failed", quota_period=None, key=None, lease=None):  # type: ignore[no-untyped-def]
    """An analysis with an explicit creation instant, for tests of the month rules. Quota period is optional."""
    import uuid
    import json as _json

    analysis_id = uuid.uuid4()
    finished = status != "running"
    _exec(
        raw_engine,
        "INSERT INTO analyses (id, organization_id, requested_by, idempotency_key, input_hash, prompt_version,"
        " model_requested, deterministic_report, status, error_code, lease_expires_at, used_fallback, created_at,"
        " completed_at, quota_period_start) VALUES (:id, :org, :user, :key, :hash, 'analysis-v1', :model,"
        " CAST(:report AS jsonb), :status, :error, :lease, false, :created, :completed, :quota)",
        {
            "id": analysis_id, "org": org, "user": user, "key": key or f"key-raw-{analysis_id.hex[:12]}",
            "hash": HASH, "model": PRIMARY, "report": _json.dumps(REPORT), "status": status,
            "error": "INTERNAL_ERROR" if finished else None,
            "lease": None if finished else (lease or created + timedelta(days=3650)),
            "created": created, "completed": created if finished else None, "quota": quota_period,
        },
    )
    return analysis_id


def _raw_call(raw_engine, org, analysis_id, *, created, status="failed", attempt=1, step="initial", role="primary", cost=None, prompt=None):  # type: ignore[no-untyped-def]
    reported = prompt is not None or cost is not None
    _exec(
        raw_engine,
        "INSERT INTO analysis_usage (organization_id, analysis_id, attempt_number, step, model_role, model, status,"
        " error_code, usage_reported, prompt_tokens, provider_cost, created_at, completed_at)"
        " VALUES (:org, :analysis, :attempt, :step, :role, :model, :status, :error, :reported, :prompt, :cost,"
        " :created, :completed)",
        {
            "org": org, "analysis": analysis_id, "attempt": attempt, "step": step, "role": role, "model": PRIMARY,
            "status": status, "error": None if status == "succeeded" else "AI_HTTP_ERROR" if status == "failed" else "ANALYSIS_ABANDONED",
            "reported": reported, "prompt": prompt, "cost": cost, "created": created,
            "completed": created if status != "reserved" else None,
        },
    )


# --- who may read it ---------------------------------------------------------------------------------------------

def test_owner_reads_exact_counts_of_calls_fallback_and_partial_consumption(api_client, database, provider, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ANALYSIS_QUOTA_ENFORCED", raising=False)
    org = owner(database, slug="usr-owner", subject="usr-owner-1")
    set_quota(database, org, limit=10)
    provider.replies = [httpx.Response(503), reply(VALID, '{"prompt_tokens":812,"completion_tokens":430,"cost":0.00012345}')]
    assert post(api_client, org, "usr-owner-1", "key-usr-owner-01").status_code == 201
    provider.replies = [reply(VALID)]  # answered without usage
    other = {"data": {**BODY["data"], "investment": 3300}}
    assert post(api_client, org, "usr-owner-1", "key-usr-owner-02", other).status_code == 201

    response = _report(api_client, org, "usr-owner-1", _current())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analyses"] == {"requested": 2, "succeeded": 2, "failed": 0, "running": 0, "without_quota_record": 0}
    calls = body["provider_calls"]
    assert (calls["total"], calls["succeeded"], calls["failed"], calls["reserved"], calls["unknown"]) == (3, 2, 1, 0, 0)
    assert calls["tokens"]["prompt_total"] == 812 and calls["tokens"]["completion_total"] == 430
    assert calls["tokens"]["calls_with_prompt_reported"] == 1
    assert calls["cost"]["known_total"] == "0.00012345"
    assert calls["cost"]["calls_with_cost"] == 1 and calls["cost"]["calls_without_cost"] == 2
    assert calls["cost"]["completeness"] == "partial"
    assert body["quota"]["configured"] is True and body["quota"]["monthly_analysis_limit"] == 10
    assert body["quota"]["enforcement_enabled"] is False
    assert body["quota"]["remaining_available"] is False and body["quota"]["remaining"] is None
    assert body["quota"]["units_consumed"] == 2 and body["quota"]["units_released"] == 0
    assert body["quota"]["reconciled"] is True


def test_analyst_and_client_viewer_are_refused(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-roles", subject="usr-owner-2")
    add_member(database, organization_id=org, subject="usr-analyst-2", role=Role.ANALYST)
    add_member(database, organization_id=org, subject="usr-viewer-2", role=Role.CLIENT_VIEWER)

    for subject in ("usr-analyst-2", "usr-viewer-2"):
        response = _report(api_client, org, subject, _current())
        assert response.status_code == 403
        assert response.json() == {"detail": "FORBIDDEN"}


def test_an_outsider_and_another_organizations_owner_get_not_found(api_client, database, provider):  # type: ignore[no-untyped-def]
    org_a = owner(database, slug="usr-out-a", subject="usr-owner-3")
    seed(database, role=None, subject="usr-outsider-3", org_slug="usr-outsider-3")
    owner(database, slug="usr-out-b", subject="usr-owner-b3")

    assert _report(api_client, org_a, "usr-outsider-3", _current()).status_code == 404
    assert _report(api_client, org_a, "usr-owner-b3", _current()).status_code == 404


def test_the_request_needs_an_identity(api_client, database):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-anon", subject="usr-owner-4")

    assert api_client.get(_path(org), params={"period": _current()}).status_code == 401


# --- period validation and empty months ----------------------------------------------------------------------------

def test_invalid_periods_are_refused(api_client, database):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-period", subject="usr-owner-5")

    for raw in ("2026-13", "2026-00", "2026-1", "26-10", "1999-10", "2026-10-01", "2026/10", "", "octubre"):
        assert _report(api_client, org, "usr-owner-5", raw).status_code == 422, raw


def test_an_omitted_period_means_the_current_utc_month(api_client, database):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-default", subject="usr-owner-6")

    body = _report(api_client, org, "usr-owner-6").json()

    assert body["period"] == _current()
    assert body["period_start"].endswith("-01")


def test_a_month_without_activity_has_no_totals_at_all(api_client, database):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-empty", subject="usr-owner-7")

    body = _report(api_client, org, "usr-owner-7", "2030-01").json()

    assert body["has_activity"] is False
    assert body["analyses"]["requested"] == 0 and body["provider_calls"]["total"] == 0
    assert body["provider_calls"]["cost"]["known_total"] is None
    assert body["provider_calls"]["cost"]["completeness"] == "no_calls"
    assert body["provider_calls"]["tokens"]["prompt_total"] is None


# --- calls, retries, replays and unknown values ------------------------------------------------------------------

def test_a_format_retry_is_a_second_call_and_not_a_second_analysis(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-retry", subject="usr-owner-8")
    provider.replies = [reply(INVALID, '{"cost":0.001}'), reply(VALID, '{"cost":0.002}')]
    assert post(api_client, org, "usr-owner-8", "key-usr-retry-01").status_code == 201

    body = _report(api_client, org, "usr-owner-8", _current()).json()

    assert body["analyses"]["requested"] == 1
    assert body["provider_calls"]["total"] == 2
    assert body["provider_calls"]["cost"]["known_total"] == "0.00300000"
    assert body["provider_calls"]["cost"]["completeness"] == "complete"


def test_replays_never_duplicate_analyses_or_calls(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-replay", subject="usr-owner-9")
    provider.replies = [reply(VALID, '{"cost":0.0004}')]
    assert post(api_client, org, "usr-owner-9", "key-usr-replay-01").status_code == 201
    assert post(api_client, org, "usr-owner-9", "key-usr-replay-01").status_code == 200
    assert post(api_client, org, "usr-owner-9", "key-usr-replay-01").status_code == 200

    body = _report(api_client, org, "usr-owner-9", _current()).json()

    assert body["analyses"]["requested"] == 1
    assert body["provider_calls"]["total"] == 1
    assert provider.calls == 1


def test_an_explicit_zero_cost_is_a_known_zero(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-zero", subject="usr-owner-10")
    provider.replies = [reply(VALID, '{"prompt_tokens":4,"cost":0}')]
    assert post(api_client, org, "usr-owner-10", "key-usr-zero-0001").status_code == 201

    cost = _report(api_client, org, "usr-owner-10", _current()).json()["provider_calls"]["cost"]

    assert cost["known_total"] == "0.00000000"
    assert cost["explicit_zero_cost_calls"] == 1 and cost["calls_without_cost"] == 0
    assert cost["completeness"] == "complete"


def test_an_unknown_cost_is_never_reported_as_zero(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-unknown", subject="usr-owner-11")
    provider.replies = [reply(VALID, '{"prompt_tokens":4}')]
    assert post(api_client, org, "usr-owner-11", "key-usr-unknown-01").status_code == 201

    body = _report(api_client, org, "usr-owner-11", _current()).json()
    cost = body["provider_calls"]["cost"]

    assert cost["known_total"] is None and cost["completeness"] == "none"
    assert cost["calls_without_cost"] == 1 and cost["calls_with_cost"] == 0
    assert body["provider_calls"]["tokens"]["prompt_total"] == 4


def test_decimal_sums_are_exact_and_serialized_as_text(api_client, database, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-decimal", subject="usr-owner-12")
    user = seed(database, role=None, subject="usr-dec-user", org_slug="usr-dec-user")[1]
    created = datetime(2030, 5, 10, 12, tzinfo=UTC)
    analysis = _raw_analysis(raw_engine, org, user, created=created, status="running", quota_period=None)
    from decimal import Decimal

    # A real order the database accepts: primary fails (charged), fallback answers, then one format retry.
    calls = [
        ("initial", "primary", "failed", "AI_EMPTY", Decimal("0.00000001")),
        ("initial", "fallback", "succeeded", None, Decimal("0.00000001")),
        ("format_retry", "primary", "succeeded", None, Decimal("0.1")),
    ]
    for number, (step, role, status, error, amount) in enumerate(calls, 1):
        _raw_call(raw_engine, org, analysis, created=created, status=status, attempt=number,
                  step=step, role=role, cost=amount, prompt=1)

    cost = _report(api_client, org, "usr-owner-12", "2030-05").json()["provider_calls"]["cost"]

    assert cost["known_total"] == "0.10000002"
    assert isinstance(cost["known_total"], str)


# --- quota state -------------------------------------------------------------------------------------------------

def test_enforced_and_configured_quota_reports_the_remaining_units(api_client, database, provider, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "true")
    org = owner(database, slug="usr-quota-on", subject="usr-owner-13")
    set_quota(database, org, limit=5)
    provider.replies = [reply(VALID), reply(VALID)]
    assert post(api_client, org, "usr-owner-13", "key-usr-quota-01").status_code == 201
    assert post(api_client, org, "usr-owner-13", "key-usr-quota-02", {"data": {**BODY["data"], "investment": 7}}).status_code == 201

    quota = _report(api_client, org, "usr-owner-13", _current()).json()["quota"]

    assert quota["enforcement_enabled"] is True and quota["remaining_available"] is True
    assert quota["remaining"] == 3 and quota["units_consumed"] == 2 and quota["reconciled"] is True


def test_a_configured_limit_with_enforcement_off_reports_no_remaining(api_client, database, provider, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "false")
    org = owner(database, slug="usr-quota-off", subject="usr-owner-14")
    set_quota(database, org, limit=5)
    provider.replies = [reply(VALID)]
    assert post(api_client, org, "usr-owner-14", "key-usr-quota-off-1").status_code == 201

    quota = _report(api_client, org, "usr-owner-14", _current()).json()["quota"]

    assert quota["remaining"] is None and quota["remaining_available"] is False
    assert quota["units_consumed"] == 1  # still counted, never refused


def test_no_quota_row_is_reported_as_not_configured(api_client, database, provider, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "true")
    org = owner(database, slug="usr-quota-none", subject="usr-owner-15")

    quota = _report(api_client, org, "usr-owner-15", _current()).json()["quota"]

    assert quota["configured"] is False and quota["monthly_analysis_limit"] is None
    assert quota["remaining"] is None and quota["remaining_available"] is False


def test_a_failure_without_a_call_gives_its_unit_back_and_reconciles(api_client, database, provider, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "true")
    org = owner(database, slug="usr-release", subject="usr-owner-16")
    set_quota(database, org, limit=5)
    monkeypatch.delenv("OPENROUTER_API_KEY")  # the analysis fails before any provider call: AI_NOT_CONFIGURED

    created = post(api_client, org, "usr-owner-16", "key-usr-release-1")
    assert created.json()["status"] == "failed"

    quota = _report(api_client, org, "usr-owner-16", _current()).json()["quota"]

    assert quota["analyses_counted"] == 1
    assert quota["units_consumed"] == 0 and quota["units_released"] == 1
    assert quota["reconciled"] is True and quota["remaining"] == 5


def test_history_without_a_quota_record_counts_as_analyses_but_never_as_units(api_client, database, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-history", subject="usr-owner-17")
    user = seed(database, role=None, subject="usr-hist-user", org_slug="usr-hist-user")[1]
    created = datetime(2030, 6, 3, 9, tzinfo=UTC)
    _raw_analysis(raw_engine, org, user, created=created, status="failed", quota_period=None)

    body = _report(api_client, org, "usr-owner-17", "2030-06").json()

    assert body["analyses"]["requested"] == 1 and body["analyses"]["without_quota_record"] == 1
    assert body["quota"]["analyses_counted"] == 0 and body["quota"]["units_consumed"] == 0
    assert body["quota"]["reconciled"] is True


# --- UTC month rules ------------------------------------------------------------------------------------------------

def test_the_utc_instant_decides_the_month(api_client, database, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-utc", subject="usr-owner-18")
    user = seed(database, role=None, subject="usr-utc-user", org_slug="usr-utc-user")[1]
    # 2030-10-31 23:30 in UTC-5 is 2030-11-01 04:30 UTC: it belongs to November.
    local_late = datetime(2030, 10, 31, 23, 30, tzinfo=UTC) + timedelta(hours=5)
    _raw_analysis(raw_engine, org, user, created=local_late, status="failed")

    october = _report(api_client, org, "usr-owner-18", "2030-10").json()
    november = _report(api_client, org, "usr-owner-18", "2030-11").json()

    assert october["analyses"]["requested"] == 0
    assert november["analyses"]["requested"] == 1


def test_a_call_stays_in_the_month_of_its_analysis_even_when_it_is_timestamped_later(api_client, database, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-cross", subject="usr-owner-19")
    user = seed(database, role=None, subject="usr-cross-user", org_slug="usr-cross-user")[1]
    started = datetime(2030, 10, 31, 23, 59, tzinfo=UTC)
    analysis = _raw_analysis(raw_engine, org, user, created=started, status="running")
    _raw_call(raw_engine, org, analysis, created=datetime(2030, 11, 1, 0, 1, tzinfo=UTC), status="succeeded",
              cost=None, prompt=10)

    october = _report(api_client, org, "usr-owner-19", "2030-10").json()
    november = _report(api_client, org, "usr-owner-19", "2030-11").json()

    assert october["analyses"]["running"] == 1 and october["provider_calls"]["total"] == 1
    assert november["provider_calls"]["total"] == 0 and november["analyses"]["requested"] == 0


# --- isolation, sensitive data and the contract ---------------------------------------------------------------------

def test_another_organizations_activity_never_enters_the_report(api_client, database, raw_engine):  # type: ignore[no-untyped-def]
    org_a = owner(database, slug="usr-iso-a", subject="usr-owner-20")
    org_b = owner(database, slug="usr-iso-b", subject="usr-owner-b20")
    user_b = seed(database, role=None, subject="usr-iso-user", org_slug="usr-iso-user")[1]
    created = datetime(2030, 7, 8, 8, tzinfo=UTC)
    analysis_b = _raw_analysis(raw_engine, org_b, user_b, created=created, status="running")
    _raw_call(raw_engine, org_b, analysis_b, created=created, status="succeeded", cost=0.5, prompt=99)

    body = _report(api_client, org_a, "usr-owner-20", "2030-07").json()

    assert body["analyses"]["requested"] == 0 and body["provider_calls"]["total"] == 0
    assert body["provider_calls"]["cost"]["known_total"] is None


def test_the_report_never_contains_prompts_markdown_or_campaign_text(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-private", subject="usr-owner-21")
    provider.replies = [reply(VALID, '{"cost":0.001}')]
    secret_body = {"data": {"rawInput": "TEXTO-SECRETO-XYZ", "campaign": "CAMPAÑA-SECRETA", "investment": 1000}}
    assert post(api_client, org, "usr-owner-21", "key-usr-private-1", secret_body).status_code == 201

    text_out = _report(api_client, org, "usr-owner-21", _current()).text

    for marker in ("TEXTO-SECRETO-XYZ", "CAMPAÑA-SECRETA", "Estado general", "deterministic", "unit-test-secret"):
        assert marker not in text_out


def test_the_response_has_exactly_the_documented_fields(api_client, database):  # type: ignore[no-untyped-def]
    from app.domain.schemas import UsageReportRead

    org = owner(database, slug="usr-shape", subject="usr-owner-22")
    body = _report(api_client, org, "usr-owner-22", _current()).json()

    assert set(body) == set(UsageReportRead.model_fields)
    assert set(body["quota"]) == {"configured", "plan", "monthly_analysis_limit", "enforcement_enabled",
                                  "analyses_counted", "units_consumed", "units_released", "reconciled",
                                  "remaining_available", "remaining"}


def test_an_invalid_enforcement_value_refuses_the_report(api_client, database, monkeypatch):  # type: ignore[no-untyped-def]
    org = owner(database, slug="usr-bad-flag", subject="usr-owner-23")
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "sometimes")

    response = _report(api_client, org, "usr-owner-23", _current())

    assert response.status_code == 503 and response.json() == {"detail": "QUOTA_CONFIGURATION_INVALID"}


def test_openapi_documents_the_report_contract(api_client, database):  # type: ignore[no-untyped-def]
    from app.main import app

    spec = app.openapi()
    operation = spec["paths"]["/api/v1/organizations/{organization_id}/usage"]["get"]
    names = {p["name"] for p in operation["parameters"]}
    period = next(p for p in operation["parameters"] if p["name"] == "period")

    assert names == {"organization_id", "period"}
    assert period["schema"]["anyOf"][0]["pattern"] == "^(20[2-9][0-9])-(0[1-9]|1[0-2])$"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/UsageReportRead"}
    assert {"403", "404", "422", "503"} <= set(operation["responses"])
    assert {"known_total", "completeness"} <= set(spec["components"]["schemas"]["UsageCostRead"]["properties"])
    json.dumps(spec)  # the whole document stays serializable
