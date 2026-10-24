"""Shared fixtures. Database tests run only against TEST_DATABASE_URL, and only if its name ends in _test."""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import DatabaseSettings
from app.db.session import Database

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; database tests need a PostgreSQL test database")
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("TEST_DATABASE_URL must use the postgresql+asyncpg:// scheme")
    database_name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not database_name.endswith("_test"):
        raise RuntimeError("Refusing to run database tests: the database name must end in _test")
    return url


def run(coroutine):  # type: ignore[no-untyped-def]
    return asyncio.run(coroutine)


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return config


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    url = _test_database_url()
    previous = os.environ.get("DATABASE_MIGRATION_URL")
    os.environ["DATABASE_MIGRATION_URL"] = url
    try:
        command.upgrade(alembic_config(), "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_MIGRATION_URL", None)
        else:
            os.environ["DATABASE_MIGRATION_URL"] = previous


@pytest.fixture
def database(test_database_url: str) -> Iterator[Database]:
    """A Database with NullPool, so each asyncio.run in a test gets fresh connections. Tables are emptied first."""
    settings = DatabaseSettings(url=test_database_url, pool_size=1, max_overflow=0, connect_timeout_seconds=5)
    db = Database(settings, poolclass=NullPool)

    async def truncate() -> None:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE organization_memberships, app_users, organizations RESTART IDENTITY CASCADE")
            )

    run(truncate())
    yield db
    run(db.dispose())


@pytest.fixture
def raw_engine(test_database_url: str) -> Iterator[object]:
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    yield engine
    run(engine.dispose())
