"""Prepares a fresh end-to-end database: drops and recreates it, applies migrations, and seeds identities.

Seeding uses the operator CLI (app.cli), exactly as an operator would. The E2E database name must end in _test,
so this script can never touch the development or production database.
Usage: E2E_DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:55432/rckt_e2e_test python e2e/prepare_database.py
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
# Migrations run in this process, so the backend package must be importable here too.
sys.path.insert(0, str(BACKEND))
IDENTITIES = json.loads((ROOT / "e2e" / "identities.json").read_text(encoding="utf-8"))


def configured(name: str) -> str:
    """Environment first; otherwise the repository .env. bun run does not pass .env values to child processes."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def admin_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, ""))


async def recreate_database(url: str) -> None:
    name = database_name(url)
    if not name.endswith("_test"):
        raise SystemExit("Refusing to recreate a database whose name does not end in _test")
    engine = create_async_engine(admin_url(url), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


def migrate(url: str) -> None:
    previous = os.environ.get("DATABASE_MIGRATION_URL")
    os.environ["DATABASE_MIGRATION_URL"] = url
    try:
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "migrations"))
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_MIGRATION_URL", None)
        else:
            os.environ["DATABASE_MIGRATION_URL"] = previous


def cli(url: str, *args: str) -> str:
    environment = {**os.environ, "DATABASE_URL": url}
    environment.pop("DATABASE_MIGRATION_URL", None)
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=BACKEND,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"operator procedure failed: {' '.join(args[:1])}")
    return result.stdout.strip()


def seed(url: str) -> None:
    for organization in IDENTITIES["organizations"]:
        cli(url, "create-organization", "--name", organization["name"], "--slug", organization["slug"])
    for user in IDENTITIES["users"]:
        cli(url, "provision-user", "--subject", user["subject"], "--email", user["email"])
        for membership in user["memberships"]:
            cli(
                url,
                "grant-membership",
                "--organization-slug", membership["organization"],
                "--role", membership["role"],
                "--subject", user["subject"],
            )


def main() -> None:
    url = configured("E2E_DATABASE_URL")
    if not url.startswith("postgresql+asyncpg://"):
        raise SystemExit("Set E2E_DATABASE_URL to a postgresql+asyncpg:// URL of a database whose name ends in _test")
    asyncio.run(recreate_database(url))
    migrate(url)
    seed(url)
    print(f"end-to-end database '{database_name(url)}' prepared")


if __name__ == "__main__":
    main()
