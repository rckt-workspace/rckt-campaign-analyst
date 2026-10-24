from fastapi.testclient import TestClient
from app.api import routes
from app.main import app

client = TestClient(app)

BASE_PAYLOAD = {
    "rawInput": "Campaña: Test",
    "campaign": "Test",
    "investment": 1000,
    "clicks": 100,
    "impressions": 2000,
}


def test_health_without_provider_key():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_input_rejects_invalid_payload():
    assert client.post("/api/v1/campaigns/analyze", json={"rawInput": "x", "ctr": "non-number"}).status_code == 422
    assert client.post("/api/v1/campaigns/analyze", json={"rawInput": "x", "unexpected": 1}).status_code == 422
    assert client.post("/api/v1/campaigns/analyze", json={"rawInput": "x" * 12001}).status_code == 422


def test_analysis_api_preserves_frontend_contract(monkeypatch):
    async def fake_analysis(payload):
        assert payload.campaign == "Test"
        assert payload.clicks == 100
        return "## Estado general\n⚪ Información insuficiente\n## Resumen ejecutivo\nPrueba."
    monkeypatch.setattr(routes, "request_campaign_analysis", fake_analysis)
    response = client.post("/api/v1/campaigns/analyze", json=BASE_PAYLOAD)
    assert response.status_code == 200
    assert response.json()["analysis"].startswith("## Estado general")


def test_missing_key_returns_safe_message(monkeypatch):
    async def unavailable(_):
        from app.core.config import AIConfigurationError
        raise AIConfigurationError("OPENROUTER_API_KEY")
    monkeypatch.setattr(routes, "request_campaign_analysis", unavailable)
    response = client.post("/api/v1/campaigns/analyze", json=BASE_PAYLOAD)
    assert response.status_code == 503
    assert response.json() == {"detail": "AI backend unavailable"}


def test_upstream_error_does_not_leak_secret(monkeypatch):
    async def unavailable(_):
        from app.integrations.openrouter import AIProviderError
        raise AIProviderError("AI_401")
    monkeypatch.setattr(routes, "request_campaign_analysis", unavailable)
    response = client.post("/api/v1/campaigns/analyze", json=BASE_PAYLOAD)
    assert response.status_code == 502
    assert "AI_401" not in response.text
