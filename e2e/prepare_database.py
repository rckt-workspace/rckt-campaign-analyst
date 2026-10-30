"""Prepares a fresh end-to-end database: drops and recreates it, applies migrations, and seeds identities.

Seeding uses the operator CLI (app.cli), exactly as an operator would. The E2E database name must end in _test,
so this script can never touch the development or production database.
Usage: the four E2E_*_DATABASE_URL variables set (see .env.example), then: python e2e/prepare_database.py
Procedure and limits: docs/staging-deploy.md, section "Pruebas E2E".
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


MIGRATOR_ROLE = "rckt_migrator"


async def recreate_database(admin: str) -> None:
    """Drops and recreates the test database, owned by the migrator role (which must already exist: see
    scripts/db_roles/provision_roles.sql). The administrative URL is used only for this step."""
    name = database_name(admin)
    if not name.endswith("_test"):
        raise SystemExit("Refusing to recreate a database whose name does not end in _test")
    engine = create_async_engine(admin_url(admin), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            await connection.execute(text(f'CREATE DATABASE "{name}" OWNER "{MIGRATOR_ROLE}"'))
    finally:
        await engine.dispose()


def migrate(url: str) -> None:
    """Applies migrations as rckt_migrator. This process never serves requests and never seeds data."""
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


def cli(operator: str, *args: str) -> str:
    """Runs one operator procedure. The CLI reads only DATABASE_OPERATOR_URL (login of an rckt_operator member)."""
    environment = {**os.environ, "DATABASE_OPERATOR_URL": operator}
    environment.pop("DATABASE_URL", None)
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


def seed(operator: str) -> None:
    for organization in IDENTITIES["organizations"]:
        cli(operator, "create-organization", "--name", organization["name"], "--slug", organization["slug"])
    for user in IDENTITIES["users"]:
        cli(operator, "provision-user", "--subject", user["subject"], "--email", user["email"])
        for membership in user["memberships"]:
            cli(
                operator,
                "grant-membership",
                "--organization-slug", membership["organization"],
                "--role", membership["role"],
                "--subject", user["subject"],
            )


def require_test_url(name: str, value: str) -> str:
    if not value.startswith("postgresql+asyncpg://") or not database_name(value).endswith("_test"):
        raise SystemExit(f"{name} must be a postgresql+asyncpg:// URL of a database whose name ends in _test")
    return value


def main() -> None:
    # Four identities, each for one purpose (docs/staging-deploy.md). None of them is the runtime login by accident.
    admin = require_test_url("E2E_ADMIN_DATABASE_URL", configured("E2E_ADMIN_DATABASE_URL"))
    migration = require_test_url("E2E_MIGRATION_DATABASE_URL", configured("E2E_MIGRATION_DATABASE_URL"))
    operator = require_test_url("E2E_OPERATOR_DATABASE_URL", configured("E2E_OPERATOR_DATABASE_URL"))
    app = require_test_url("E2E_APP_DATABASE_URL", configured("E2E_APP_DATABASE_URL"))
    asyncio.run(recreate_database(admin))
    migrate(migration)
    seed(operator)
    print(f"end-to-end database '{database_name(admin)}' prepared. The backend runs with E2E_APP_DATABASE_URL (rckt_app).")
    del app  # validated above; the backend receives it through the Playwright environment, never through this script


if __name__ == "__main__":
    main()
