"""Who may read consumption. Owner only; every other role reads the report without the consumption keys.

The keys are absent, not null and not zero: a response for a non-owner says nothing about consumption. Each request
goes through the real HTTP API with a simulated provider. No test reaches OpenRouter.
Policy: docs/PHASE2_DESIGN.md (section 4, "Ver uso, costos y auditoría") and docs/PHASE2_PROVIDER_USAGE.md.
"""

from app.domain.access import Role
from tests.api_fixtures import api_client, token_verifier  # noqa: F401  (pytest fixtures)
from tests.auth_helpers import make_token
from tests.test_analysis_api import BODY, INVALID, VALID, ok, owner, post, provider  # noqa: F401  (pytest fixtures)
from tests.test_campaign_api import add_member
from tests.test_provider_usage import reply

CONSUMPTION_KEYS = {"usage_reported", "prompt_tokens", "completion_tokens", "cost_micros", "provider_cost"}
REPORTED_COST = "0.00012345"
OTHER_BODY = {"data": {**BODY["data"], "investment": 2500}}


def _analysis_path(organization_id) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/organizations/{organization_id}/analyses"


def _get(api_client, organization_id, subject: str, analysis_id: str):  # type: ignore[no-untyped-def]
    return api_client.get(f"{_analysis_path(organization_id)}/{analysis_id}", headers={"Authorization": f"Bearer {make_token(subject)}"})


def _consumption_keys_in(body) -> set[str]:  # type: ignore[no-untyped-def]
    found = set()
    if isinstance(body, dict):
        found |= CONSUMPTION_KEYS & set(body)
        for value in body.values():
            found |= _consumption_keys_in(value)
    elif isinstance(body, list):
        for item in body:
            found |= _consumption_keys_in(item)
    return found


def _owner_creates_with_cost(api_client, organization_id, subject: str, key: str, provider, usage: str | None = None):  # type: ignore[no-untyped-def]
    provider.replies = [reply(VALID, usage or '{"prompt_tokens":812,"completion_tokens":430,"cost":' + REPORTED_COST + "}")]
    response = post(api_client, organization_id, subject, key)
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- owner, analyst and viewer reading the same report -----------------------------------------------------------

def test_owner_reads_the_consumption_of_a_report(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="vis-owner", subject="vis-owner-1")
    analysis_id = _owner_creates_with_cost(api_client, org, "vis-owner-1", "key-vis-owner-01", provider)

    body = _get(api_client, org, "vis-owner-1", analysis_id).json()

    attempt = body["attempts"][0]
    assert attempt["usage_reported"] is True
    assert attempt["prompt_tokens"] == 812 and attempt["completion_tokens"] == 430
    assert attempt["provider_cost"] == REPORTED_COST  # exact decimal text, never a float


def test_analyst_reads_the_same_report_without_any_consumption(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="vis-analyst", subject="vis-owner-2")
    add_member(database, organization_id=org, subject="vis-analyst-2", role=Role.ANALYST)
    analysis_id = _owner_creates_with_cost(api_client, org, "vis-owner-2", "key-vis-analyst-01", provider)

    response = _get(api_client, org, "vis-analyst-2", analysis_id)

    assert response.status_code == 200
    body = response.json()
    assert body["analysis_markdown"].startswith("## Estado general")
    assert _consumption_keys_in(body) == set()
    assert REPORTED_COST not in response.text


def test_client_viewer_reads_the_report_without_any_consumption(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="vis-viewer", subject="vis-owner-3")
    add_member(database, organization_id=org, subject="vis-viewer-3", role=Role.CLIENT_VIEWER)
    analysis_id = _owner_creates_with_cost(api_client, org, "vis-owner-3", "key-vis-viewer-01", provider)

    response = _get(api_client, org, "vis-viewer-3", analysis_id)

    assert response.status_code == 200
    assert _consumption_keys_in(response.json()) == set()
    assert REPORTED_COST not in response.text


def test_analyst_receives_no_consumption_for_the_analysis_it_just_created(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="vis-create", subject="vis-owner-4")
    add_member(database, organization_id=org, subject="vis-analyst-4", role=Role.ANALYST)
    provider.replies = [reply(VALID, '{"prompt_tokens":5,"cost":' + REPORTED_COST + "}")]

    response = post(api_client, org, "vis-analyst-4", "key-vis-create-01")

    assert response.status_code == 201
    assert response.json()["analysis_markdown"].startswith("## Estado general")
    assert _consumption_keys_in(response.json()) == set()
    assert REPORTED_COST not in response.text


# --- isolation, unreported consumption and history ----------------------------------------------------------------

def test_another_organization_cannot_read_the_report_or_its_consumption(api_client, database, provider):  # type: ignore[no-untyped-def]
    org_a = owner(database, slug="vis-iso-a", subject="vis-owner-5")
    org_b = owner(database, slug="vis-iso-b", subject="vis-owner-b5")
    analysis_id = _owner_creates_with_cost(api_client, org_a, "vis-owner-5", "key-vis-iso-0001", provider)

    # The owner of B, asking through B's path for A's analysis: the analysis is not in B.
    assert _get(api_client, org_b, "vis-owner-b5", analysis_id).status_code == 404
    # The owner of B, asking through A's path: B has no membership in A.
    assert _get(api_client, org_a, "vis-owner-b5", analysis_id).status_code == 404


def test_unreported_consumption_is_shown_as_unreported_for_the_owner_never_as_zero(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="vis-null", subject="vis-owner-6")
    add_member(database, organization_id=org, subject="vis-analyst-6", role=Role.ANALYST)
    provider.replies = [reply(VALID)]  # the provider answered without a usage object
    analysis_id = post(api_client, org, "vis-owner-6", "key-vis-null-0001").json()["id"]

    owner_view = _get(api_client, org, "vis-owner-6", analysis_id).json()["attempts"][0]
    analyst_view = _get(api_client, org, "vis-analyst-6", analysis_id).json()["attempts"][0]

    assert owner_view["usage_reported"] is False
    assert owner_view["prompt_tokens"] is None and owner_view["provider_cost"] is None
    assert owner_view["cost_micros"] is None
    assert not CONSUMPTION_KEYS & set(analyst_view)


def test_idempotent_replay_keeps_each_role_view_without_a_new_call(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="vis-replay", subject="vis-owner-7")
    add_member(database, organization_id=org, subject="vis-analyst-7", role=Role.ANALYST)
    provider.replies = [reply(VALID, '{"prompt_tokens":9,"cost":' + REPORTED_COST + "}")]
    first = post(api_client, org, "vis-analyst-7", "key-vis-replay-01")

    analyst_replay = post(api_client, org, "vis-analyst-7", "key-vis-replay-01")
    owner_replay = post(api_client, org, "vis-owner-7", "key-vis-replay-01")

    assert first.status_code == 201
    assert analyst_replay.status_code == 200 and owner_replay.status_code == 200
    assert analyst_replay.json()["id"] == owner_replay.json()["id"] == first.json()["id"]
    assert _consumption_keys_in(analyst_replay.json()) == set()
    assert owner_replay.json()["attempts"][0]["provider_cost"] == REPORTED_COST
    assert provider.calls == 1


def test_the_history_list_never_carries_consumption_for_anyone(api_client, database, provider):  # type: ignore[no-untyped-def]
    org = owner(database, slug="vis-list", subject="vis-owner-8")
    add_member(database, organization_id=org, subject="vis-analyst-8", role=Role.ANALYST)
    _owner_creates_with_cost(api_client, org, "vis-owner-8", "key-vis-list-0001", provider)

    for subject in ("vis-owner-8", "vis-analyst-8"):
        page = api_client.get(_analysis_path(org), headers={"Authorization": f"Bearer {make_token(subject)}"}).json()
        assert page["items"], "the history must list the analysis"
        assert _consumption_keys_in(page) == set()
        assert "attempts" not in page["items"][0]
