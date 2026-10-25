"""Database configuration and failure handling. These tests need no PostgreSQL server."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app.core.config import DatabaseConfigurationError, DatabaseSettings, get_database_settings
from app.db.session import Database, DatabaseUnavailableError
from app.domain.access import PAID_AI_ROLES, Role, can_run_paid_ai

SECRET_PASSWORD = "s3cret-pw-for-tests"
UNREACHABLE_URL = f"postgresql+asyncpg://rckt:{SECRET_PASSWORD}@127.0.0.1:1/rckt"


def test_database_is_disabled_when_url_is_missing(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert get_database_settings() is None


def test_database_url_must_use_async_postgresql_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"postgresql://rckt:{SECRET_PASSWORD}@db:5432/rckt")
    with pytest.raises(DatabaseConfigurationError) as info:
        get_database_settings()
    assert SECRET_PASSWORD not in str(info.value)


def test_pool_settings_are_validated(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    monkeypatch.setenv("DATABASE_POOL_SIZE", "500")
    with pytest.raises(DatabaseConfigurationError) as info:
        get_database_settings()
    assert SECRET_PASSWORD not in str(info.value)


def test_connection_failure_is_controlled_and_leaks_no_details():
    database = Database(DatabaseSettings(url=UNREACHABLE_URL, pool_size=1, max_overflow=0, connect_timeout_seconds=1), poolclass=NullPool)

    async def attempt():
        async with database.session() as session:
            await session.execute(text("SELECT 1"))

    try:
        with pytest.raises(DatabaseUnavailableError) as info:
            asyncio.run(attempt())
        rendered = f"{info.value!s} {info.value!r} {info.value.__cause__!r}"
        assert SECRET_PASSWORD not in rendered
        assert "127.0.0.1" not in str(info.value)
    finally:
        asyncio.run(database.dispose())


def test_readiness_reports_missing_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"detail": "database not configured"}


def test_readiness_reports_unreachable_database_without_details(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "1")
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    assert SECRET_PASSWORD not in response.text


def test_liveness_does_not_depend_on_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("role", [Role.OWNER, Role.ANALYST])
def test_roles_that_may_spend_on_ai(role):
    assert can_run_paid_ai(role) is True
    assert role in PAID_AI_ROLES


def test_client_viewer_may_not_spend_on_ai():
    assert can_run_paid_ai(Role.CLIENT_VIEWER) is False
