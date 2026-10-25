import pytest

from app.core.config import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_PRIMARY_MODEL,
    AIConfigurationError,
    get_ai_settings,
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in (
        "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "CHAT_PRIMARY_LLM", "CHAT_FALLBACK_LLM",
        "CHAT_USE_FALLBACK", "CHAT_TEMPERATURE", "CHAT_TOP_P", "CHAT_MAX_TOKENS", "CHAT_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "unit-test-secret")


def test_defaults_use_catalog_verified_models_and_token_budget(clean_env):
    settings = get_ai_settings()
    assert settings.primary_model == DEFAULT_PRIMARY_MODEL == "google/gemma-3-27b-it"
    assert settings.fallback_model == DEFAULT_FALLBACK_MODEL == "openrouter/free"
    assert settings.max_tokens == DEFAULT_MAX_TOKENS == 1200
    assert settings.timeout_seconds == 45
    assert settings.use_fallback is True


def test_retired_free_gemma_is_not_the_default():
    assert DEFAULT_PRIMARY_MODEL != "google/gemma-3-4b-it:free"


def test_missing_key_is_a_configuration_error(monkeypatch, clean_env):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(AIConfigurationError, match="OPENROUTER_API_KEY"):
        get_ai_settings()


def test_out_of_range_values_are_rejected(monkeypatch, clean_env):
    monkeypatch.setenv("CHAT_TEMPERATURE", "5")
    with pytest.raises(AIConfigurationError, match="CHAT_TEMPERATURE"):
        get_ai_settings()
