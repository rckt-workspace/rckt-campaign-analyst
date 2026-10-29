"""Audit events: what is recorded, when, by whom, that a refused write leaves nothing behind, and who may read it.

Provider calls are simulated (the analysis test provider). The operator procedures run against the test database.
Rules: docs/PHASE2_AUDIT.md. No test reaches OpenRouter.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import cli
from app.db import analysis_ledger as ledger
from app.domain.access import Role
from app.domain.audit import ALLOWED_DETAILS, AuditDraft, AuditValidationError
from app.services import audit as audit_service
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import make_token
from tests.conftest import run
from tests.test_analysis_api import BODY, VALID, ok, owner, post, provider  # noqa: F401  (pytest fixtures)
from tests.test_analysis_ledger import PRIMARY, REPORT, rows
from tests.test_campaign_api import add_member, seed
from tests.test_quota import set_quota

HASH = "c" * 64
SECRET_MARKERS = ("TEXTO-SECRETO-AUD", "CAMPAÑA-SECRETA-AUD", "Estado general", "unit-test-secret", "eyJ", "sk-or-")


def _events(raw_engine, organization_id):  # type: ignore[no-untyped-def]
    return rows(
        raw_engine,
        "SELECT id, source, actor_user_id, action, resource_type, resource_id, metadata FROM audit_events"
        " WHERE organization_id = :org ORDER BY id",
        {"org": organization_id},
    )


def _actions(raw_engine, organization_id) -> list[str]:  # type: ignore[no-untyped-def]
    return [row["action"] for row in _events(raw_engine, organization_id)]


def _audit_url(organization_id) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/organizations/{organization_id}/audit-events"


def _get(api_client, organization_id, subject: str, **params):  # type: ignore[no-untyped-def]
    return api_client.get(_audit_url(organization_id), params=params, headers={"Authorization": f"Bearer {make_token(subject)}"})


def _user_id(database, subject: str):  # type: ignore[no-untyped-def]
    async def read():  # type: ignore[no-untyped-def]
        async with database.session() as session:
            from app.db.repositories import UserRepository

            return (await UserRepository(session).get_by_subject(subject)).id

    return run(read())


# --- unit: the allow-list and the source rules ---------------------------------------------------------------------

def test_an_event_outside_the_allow_list_is_refused_before_any_write():
    org = uuid.uuid4()
    with pytest.raises(AuditValidationError):
        AuditDraft(organization_id=org, action="analysis.failed", resource_id="x", source="internal",
                   details={"error_code": "INTERNAL_ERROR", "used_fallback": False, "prompt": "texto del usuario"})
    with pytest.raises(AuditValidationError):
        AuditDraft(organization_id=org, action="analysis.failed", resource_id="x", source="internal",
                   details={"error_code": "SECRET_TOKEN_LEAK", "used_fallback": False})


@pytest.mark.parametrize("kwargs", [
    dict(action="analysis.deleted", source="internal"),
    dict(action="analysis.failed", source="cron"),
    dict(action="analysis.failed", source="user", actor_user_id=None),
    dict(action="analysis.failed", source="internal", actor_user_id=uuid.uuid4()),
    dict(action="analysis.failed", source="internal", resource_id="   "),
])
def test_source_and_actor_rules_are_enforced(kwargs):
    base = dict(organization_id=uuid.uuid4(), resource_id="r", details={"error_code": "INTERNAL_ERROR", "used_fallback": False})
    with pytest.raises(AuditValidationError):
        AuditDraft(**{**base, **kwargs})


def test_a_cli_event_has_no_invented_actor():
    draft = AuditDraft(organization_id=uuid.uuid4(), action="membership.granted", resource_id=str(uuid.uuid4()),
                       source="operator_cli", details={"role": "analyst"})
    assert draft.actor_user_id is None


@pytest.mark.parametrize("action, details", [
    ("analysis.succeeded", {"model_used": "x", "used_fallback": 1, "format_valid": True}),  # int is not bool
    ("analysis.succeeded", {"model_used": "bad model name!", "used_fallback": False, "format_valid": True}),
    ("analysis.created", {"quota_period": "2026-13"}),
    ("quota.updated", {"plan": "gold", "monthly_analysis_limit": 5, "previous_plan": None, "previous_monthly_analysis_limit": None}),
    ("quota.updated", {"plan": "pilot", "monthly_analysis_limit": -1, "previous_plan": None, "previous_monthly_analysis_limit": None}),
    ("membership.granted", {"role": "platform_admin"}),
    ("analysis.failed", {"error_code": "AI_UNKNOWN_THING", "used_fallback": False}),
    ("usage.report_accessed", {"period": "2026-10", "extra": "x"}),
])
def test_value_types_and_codes_are_checked(action, details):
    with pytest.raises(AuditValidationError):
        AuditDraft(organization_id=uuid.uuid4(), action=action, resource_id="r", source="internal", details=details)


def test_no_action_allows_free_text_or_arbitrary_objects():
    for action, fields in ALLOWED_DETAILS.items():
        assert all(kind in {"bool", "int", "period", "plan", "role", "error_code", "model"} for kind, _ in fields.values()), action


def test_the_migration_statements_contain_no_inserts():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261027_0008_audit_events.py"
    spec = importlib.util.spec_from_file_location("migration_0008", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.STATEMENTS and not any("INSERT" in statement.upper() for statement in module.STATEMENTS)


# --- events written by the real operations -------------------------------------------------------------------------

def test_a_new_analysis_records_analysis_created_with_its_user_actor(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-create", subject="aud-owner-1")
    add_member(database, organization_id=org, subject="aud-analyst-1", role=Role.ANALYST)
    provider.replies = [ok(VALID)]

    created = post(api_client, org, "aud-analyst-1", "key-aud-create-01")

    assert created.status_code == 201
    event = _events(raw_engine, org)[0]
    assert event["action"] == "analysis.created" and event["source"] == "user"
    assert str(event["actor_user_id"]) == str(_user_id(database, "aud-analyst-1"))
    assert event["resource_type"] == "analysis" and event["resource_id"] == created.json()["id"]
    # The quota month is the UTC month of the claim (the quota is counted there even when enforcement is off).
    assert event["metadata"] == {"quota_period": datetime.now(UTC).strftime("%Y-%m")}


def test_a_replay_records_nothing_new(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-replay", subject="aud-owner-2")
    provider.replies = [ok(VALID)]
    post(api_client, org, "aud-owner-2", "key-aud-replay-01")
    before = _actions(raw_engine, org)

    assert post(api_client, org, "aud-owner-2", "key-aud-replay-01").status_code == 200
    assert post(api_client, org, "aud-owner-2", "key-aud-replay-01").status_code == 200

    assert _actions(raw_engine, org) == before == ["analysis.created", "analysis.succeeded"]


def test_a_successful_analysis_records_analysis_succeeded_once(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-success", subject="aud-owner-3")
    provider.replies = [ok(VALID)]
    post(api_client, org, "aud-owner-3", "key-aud-success-1")

    succeeded = [e for e in _events(raw_engine, org) if e["action"] == "analysis.succeeded"]
    assert len(succeeded) == 1
    assert succeeded[0]["source"] == "internal" and succeeded[0]["actor_user_id"] is None
    assert succeeded[0]["metadata"] == {"model_used": "primary-test", "used_fallback": False, "format_valid": True}


def test_a_failed_analysis_records_analysis_failed_with_its_safe_code(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-failed", subject="aud-owner-4")
    provider.replies = [httpx.Response(401), httpx.Response(401)]
    post(api_client, org, "aud-owner-4", "key-aud-failed-01")

    failed = [e for e in _events(raw_engine, org) if e["action"] == "analysis.failed"]
    assert len(failed) == 1
    assert failed[0]["metadata"] == {"error_code": "AI_HTTP_ERROR", "used_fallback": False}


def test_an_abandoned_analysis_records_analysis_failed_once(database, raw_engine):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="aud-abandon", org_slug="aud-abandon")
    claim = run(ledger.claim_analysis(
        database, organization_id=org, requested_by=user, campaign_id=None, metric_entry_id=None,
        idempotency_key="key-aud-abandon-1", input_hash=HASH, prompt_version="analysis-v1", model_requested=PRIMARY,
        deterministic_report=REPORT, lease_seconds=1,
    ))

    async def expire_and_abandon():  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            await connection.execute(text("UPDATE analyses SET lease_expires_at = now() - interval '1 second' WHERE id = :id"),
                                     {"id": claim.analysis_id})
            await connection.commit()
        first = await ledger.abandon_expired(database, organization_id=org, analysis_id=claim.analysis_id)
        second = await ledger.abandon_expired(database, organization_id=org, analysis_id=claim.analysis_id)
        return first, second

    assert run(expire_and_abandon()) == (True, False)
    assert _actions(raw_engine, org) == ["analysis.created", "analysis.failed"]
    abandoned = _events(raw_engine, org)[1]
    assert abandoned["source"] == "internal" and abandoned["metadata"] == {"error_code": "ANALYSIS_ABANDONED", "used_fallback": False}


def test_an_exhausted_quota_records_no_analysis_created(api_client, database, provider, monkeypatch, raw_engine):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANALYSIS_QUOTA_ENFORCED", "true")
    org = owner(database, slug="aud-quota-out", subject="aud-owner-5")
    set_quota(database, org, limit=0)

    response = post(api_client, org, "aud-owner-5", "key-aud-quota-out")

    assert response.status_code == 429
    assert _actions(raw_engine, org) == []


def test_a_quota_change_by_the_cli_records_the_previous_and_new_values(database, raw_engine, test_database_url, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    run(cli.create_organization("Aud Cuota", "aud-cuota"))
    org = rows(raw_engine, "SELECT id FROM organizations WHERE slug = 'aud-cuota'")[0]["id"]

    cli.main(["set-analysis-quota", "--organization-slug", "aud-cuota", "--plan", "pilot", "--monthly-limit", "50"])
    cli.main(["set-analysis-quota", "--organization-slug", "aud-cuota", "--plan", "pilot", "--monthly-limit", "50"])  # unchanged
    cli.main(["set-analysis-quota", "--organization-slug", "aud-cuota", "--plan", "standard", "--monthly-limit", "80"])

    events = [e for e in _events(raw_engine, org) if e["action"] == "quota.updated"]
    assert [e["metadata"] for e in events] == [
        {"plan": "pilot", "monthly_analysis_limit": 50, "previous_plan": None, "previous_monthly_analysis_limit": None},
        {"plan": "standard", "monthly_analysis_limit": 80, "previous_plan": "pilot", "previous_monthly_analysis_limit": 50},
    ]
    assert all(e["source"] == "operator_cli" and e["actor_user_id"] is None for e in events)


def test_a_membership_granted_by_the_cli_records_an_operator_event(database, raw_engine, test_database_url, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    run(cli.create_organization("Aud Miembro", "aud-miembro"))
    run(cli.provision_user("aud-sub-cli", "cli@example.com"))
    cli.main(["grant-membership", "--organization-slug", "aud-miembro", "--role", "analyst", "--subject", "aud-sub-cli"])
    org = rows(raw_engine, "SELECT id FROM organizations WHERE slug = 'aud-miembro'")[0]["id"]

    event = _events(raw_engine, org)[0]
    assert event["action"] == "membership.granted" and event["source"] == "operator_cli"
    assert event["actor_user_id"] is None and event["metadata"] == {"role": "analyst"}


def test_a_refused_quota_write_leaves_neither_the_quota_nor_its_event(database, raw_engine, test_database_url, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    run(cli.create_organization("Aud Falla", "aud-falla"))

    async def refuse(session, draft):  # type: ignore[no-untyped-def]
        raise DBAPIError("INSERT INTO audit_events", None, Exception("injected refusal"))

    monkeypatch.setattr(cli, "insert_audit_event", refuse)
    with pytest.raises(DBAPIError):
        cli.main(["set-analysis-quota", "--organization-slug", "aud-falla", "--plan", "pilot", "--monthly-limit", "9"])

    assert rows(raw_engine, "SELECT count(*) AS n FROM analysis_quotas q JOIN organizations o ON o.id = q.organization_id"
                            " WHERE o.slug = 'aud-falla'")[0]["n"] == 0


def test_a_refused_event_rolls_back_the_claim_and_its_unit(database, raw_engine, monkeypatch):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="aud-claim-fail", org_slug="aud-claim-fail")
    set_quota(database, org, limit=3)

    async def refuse(session, draft):  # type: ignore[no-untyped-def]
        raise DBAPIError("INSERT INTO audit_events", None, Exception("injected refusal"))

    monkeypatch.setattr(ledger, "insert_audit_event", refuse)
    with pytest.raises(DBAPIError):
        run(ledger.claim_analysis(
            database, organization_id=org, requested_by=user, campaign_id=None, metric_entry_id=None,
            idempotency_key="key-aud-claim-01", input_hash=HASH, prompt_version="analysis-v1", model_requested=PRIMARY,
            deterministic_report=REPORT, lease_seconds=120,
        ))

    assert rows(raw_engine, "SELECT count(*) AS n FROM analyses WHERE organization_id = :o", {"o": org})[0]["n"] == 0
    assert rows(raw_engine, "SELECT count(*) AS n FROM audit_events WHERE organization_id = :o", {"o": org})[0]["n"] == 0


def test_a_refused_finalization_leaves_the_analysis_running_and_no_event(database, raw_engine, monkeypatch):  # type: ignore[no-untyped-def]
    org, user = seed(database, role=None, subject="aud-final-fail", org_slug="aud-final-fail")
    claim = run(ledger.claim_analysis(
        database, organization_id=org, requested_by=user, campaign_id=None, metric_entry_id=None,
        idempotency_key="key-aud-final-01", input_hash=HASH, prompt_version="analysis-v1", model_requested=PRIMARY,
        deterministic_report=REPORT, lease_seconds=120,
    ))

    async def refuse(session, draft):  # type: ignore[no-untyped-def]
        raise DBAPIError("INSERT INTO audit_events", None, Exception("injected refusal"))

    monkeypatch.setattr(ledger, "insert_audit_event", refuse)
    with pytest.raises(DBAPIError):
        run(ledger.finalize_analysis(database, organization_id=org, analysis_id=claim.analysis_id,
                                     status="failed", error_code="AI_NOT_CONFIGURED"))

    assert rows(raw_engine, "SELECT status FROM analyses WHERE id = :id", {"id": claim.analysis_id})[0]["status"] == "running"
    assert [e["action"] for e in _events(raw_engine, org)] == ["analysis.created"]


# --- reading the usage report is audited, only when it is delivered -----------------------------------------------

def _usage(api_client, org, subject: str):  # type: ignore[no-untyped-def]
    return api_client.get(f"/api/v1/organizations/{org}/usage", params={"period": "2030-01"},
                          headers={"Authorization": f"Bearer {make_token(subject)}"})


def test_an_authorized_usage_read_is_recorded_for_the_owner(api_client, database, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-usage", subject="aud-owner-6")

    assert _usage(api_client, org, "aud-owner-6").status_code == 200

    event = [e for e in _events(raw_engine, org) if e["action"] == "usage.report_accessed"][0]
    assert event["source"] == "user" and str(event["actor_user_id"]) == str(_user_id(database, "aud-owner-6"))
    assert event["resource_type"] == "usage_report" and event["resource_id"] == "2030-01"
    assert event["metadata"] == {"period": "2030-01"}


def test_refused_reads_are_never_recorded_as_accesses(api_client, database, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-refused", subject="aud-owner-7")
    add_member(database, organization_id=org, subject="aud-analyst-7", role=Role.ANALYST)
    seed(database, role=None, subject="aud-outsider-7", org_slug="aud-outsider-7")

    assert _usage(api_client, org, "aud-analyst-7").status_code == 403
    assert _usage(api_client, org, "aud-outsider-7").status_code == 404
    assert "usage.report_accessed" not in _actions(raw_engine, org)


def test_a_usage_read_is_not_delivered_when_its_audit_event_cannot_be_written(api_client, database, raw_engine, monkeypatch):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-undelivered", subject="aud-owner-8")

    async def refuse(database_, draft):  # type: ignore[no-untyped-def]
        raise DBAPIError("INSERT INTO audit_events", None, Exception("injected refusal"))

    monkeypatch.setattr(audit_service, "record_event", refuse)
    response = _usage(api_client, org, "aud-owner-8")

    assert response.status_code == 503
    assert response.json() == {"detail": "AUDIT_UNAVAILABLE"}
    assert "quota" not in response.text and "provider_calls" not in response.text


# --- reading the audit trail: owner only, isolated, paginated -----------------------------------------------------

def test_only_the_owner_reads_the_audit_trail(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-read", subject="aud-owner-9")
    add_member(database, organization_id=org, subject="aud-analyst-9", role=Role.ANALYST)
    add_member(database, organization_id=org, subject="aud-viewer-9", role=Role.CLIENT_VIEWER)
    seed(database, role=None, subject="aud-outsider-9", org_slug="aud-outsider-9")

    assert _get(api_client, org, "aud-owner-9").status_code == 200
    assert _get(api_client, org, "aud-analyst-9").status_code == 403
    assert _get(api_client, org, "aud-viewer-9").status_code == 403
    assert _get(api_client, org, "aud-outsider-9").status_code == 404
    assert api_client.get(_audit_url(org)).status_code == 401


def test_another_organization_sees_none_of_these_events(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org_a = owner(database, slug="aud-iso-a", subject="aud-owner-10")
    org_b = owner(database, slug="aud-iso-b", subject="aud-owner-b10")
    provider.replies = [ok(VALID)]
    post(api_client, org_a, "aud-owner-10", "key-aud-iso-0001")

    assert _get(api_client, org_b, "aud-owner-b10").json()["items"] == []
    assert _get(api_client, org_a, "aud-owner-b10").status_code == 404
    assert len(_get(api_client, org_a, "aud-owner-10").json()["items"]) == 2


def test_pages_are_newest_first_and_never_overlap(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-page", subject="aud-owner-11")
    provider.replies = [ok(VALID), ok(VALID), ok(VALID)]
    for index in range(3):
        post(api_client, org, "aud-owner-11", f"key-aud-page-{index:03d}", {"data": {**BODY["data"], "investment": 100 + index}})

    everything = _get(api_client, org, "aud-owner-11", limit=100).json()
    ids = [item["id"] for item in everything["items"]]
    assert len(ids) == 6 and everything["next_cursor"] is None  # two events per analysis: created and finished
    assert ids == sorted(ids, reverse=True)

    first = _get(api_client, org, "aud-owner-11", limit=4).json()
    assert [i["id"] for i in first["items"]] == ids[:4] and first["next_cursor"] == ids[3]
    second = _get(api_client, org, "aud-owner-11", limit=4, cursor=first["next_cursor"]).json()
    assert [i["id"] for i in second["items"]] == ids[4:] and second["next_cursor"] is None
    assert set(i["id"] for i in second["items"]).isdisjoint(i["id"] for i in first["items"])


def test_the_page_size_and_cursor_are_validated(api_client, database):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-validate", subject="aud-owner-12")

    for params in ({"limit": 0}, {"limit": 101}, {"cursor": 0}, {"cursor": "x"}):
        response = api_client.get(_audit_url(org), params=params, headers={"Authorization": f"Bearer {make_token('aud-owner-12')}"})
        assert response.status_code == 422, params


def test_the_database_refuses_to_update_or_delete_an_event(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-immutable", subject="aud-owner-13")
    provider.replies = [ok(VALID)]
    post(api_client, org, "aud-owner-13", "key-aud-immutable-1")

    async def change(sql):  # type: ignore[no-untyped-def]
        async with raw_engine.connect() as connection:
            await connection.execute(text(sql), {"org": org})
            await connection.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        run(change("UPDATE audit_events SET action = 'analysis.failed' WHERE organization_id = :org"))
    with pytest.raises(DBAPIError, match="append-only"):
        run(change("DELETE FROM audit_events WHERE organization_id = :org"))


def test_the_audit_table_is_closed_to_the_managed_apis(raw_engine):  # type: ignore[no-untyped-def]
    assert rows(raw_engine, "SELECT relrowsecurity FROM pg_class WHERE relname = 'audit_events'")[0]["relrowsecurity"] is True
    grants = rows(raw_engine, "SELECT grantee FROM information_schema.role_table_grants"
                              " WHERE table_name = 'audit_events' AND grantee IN ('anon', 'authenticated', 'PUBLIC')")
    assert grants == []


def test_no_secret_or_user_text_is_stored_in_any_event(api_client, database, provider, raw_engine):  # type: ignore[no-untyped-def]
    org = owner(database, slug="aud-secrets", subject="aud-owner-14")
    provider.replies = [ok(VALID)]
    body = {"data": {"rawInput": "TEXTO-SECRETO-AUD", "campaign": "CAMPAÑA-SECRETA-AUD", "investment": 1000}}
    post(api_client, org, "aud-owner-14", "key-aud-secrets-1", body)
    _usage(api_client, org, "aud-owner-14")

    stored = json.dumps(_events(raw_engine, org), default=str)
    for marker in SECRET_MARKERS:
        assert marker not in stored, marker


# --- contract ------------------------------------------------------------------------------------------------------

def test_openapi_documents_the_audit_contract():
    from app.main import app

    spec = app.openapi()
    operation = spec["paths"]["/api/v1/organizations/{organization_id}/audit-events"]["get"]
    names = {p["name"] for p in operation["parameters"]}
    assert names == {"organization_id", "cursor", "limit"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/AuditEventPageRead"}
    assert {"403", "404", "422"} <= set(operation["responses"])
    assert "metadata" in spec["components"]["schemas"]["AuditEventRead"]["properties"]
    assert not any(method in spec["paths"]["/api/v1/organizations/{organization_id}/audit-events"] for method in ("post", "put", "patch", "delete"))
    json.dumps(spec)


def test_the_response_has_exactly_the_documented_fields(api_client, database, provider):  # type: ignore[no-untyped-def]
    from app.domain.schemas import AuditEventPageRead, AuditEventRead

    org = owner(database, slug="aud-shape", subject="aud-owner-15")
    provider.replies = [ok(VALID)]
    post(api_client, org, "aud-owner-15", "key-aud-shape-0001")

    body = _get(api_client, org, "aud-owner-15").json()
    assert set(body) == set(AuditEventPageRead.model_fields)
    assert all(set(item) == set(AuditEventRead.model_fields) for item in body["items"])
