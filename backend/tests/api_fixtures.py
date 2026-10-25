"""Fixtures for HTTP tests: the real FastAPI app wired to the test database and the test identity verifier.

Test modules import these names explicitly, so pytest registers them. Nothing here is reachable from the application.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security.tokens import SupabaseTokenVerifier
from tests.auth_helpers import TEST_AUDIENCE, TEST_ISSUER, public_key_resolver


@pytest.fixture
def token_verifier() -> SupabaseTokenVerifier:
    return SupabaseTokenVerifier(issuer=TEST_ISSUER, audience=TEST_AUDIENCE, resolve_key=public_key_resolver)


@pytest.fixture
def api_client(database, token_verifier):  # type: ignore[no-untyped-def]
    """No lifespan and no network: the test sets the database and verifier directly on app.state."""
    app.state.database = database
    app.state.token_verifier = token_verifier
    try:
        yield TestClient(app)
    finally:
        app.state.database = None
        app.state.token_verifier = None


@pytest.fixture
def provider_calls(monkeypatch):  # type: ignore[no-untyped-def]
    """Replaces the paid AI call with a recorder. Any request that reaches the provider shows up here."""
    from app.api import routes

    calls: list[object] = []

    async def fake_analysis(payload):  # type: ignore[no-untyped-def]
        calls.append(payload)
        return "## Estado general\n🟡 Requiere atención\n## Resumen ejecutivo\nPrueba controlada."

    monkeypatch.setattr(routes, "request_campaign_analysis", fake_analysis)
    return calls
