"""Prepares a fresh end-to-end database: drops and recreates it, applies migrations, and seeds identities.

Destructive by design, so it is guarded. It drops the database only when ALL of these hold (refusal_reasons):
- the operator confirmed, in E2E_DISPOSABLE_INSTANCE, that the instance is disposable and used by nothing else;
- the four URLs name the same local instance and the exact database name rckt_e2e_test (a suffix is not enough);
- the instance holds no other user database (a shared or development instance always does);
- if the test database already exists, it carries the marker this script wrote when it created it.
Nothing is dropped before every check passes. The script never prints a URL or a password, only variable names and reasons.

Seeding uses the operator CLI (app.cli), exactly as an operator would.
Usage: set the four E2E_*_DATABASE_URL variables and E2E_DISPOSABLE_INSTANCE (see .env.example), then:
    python e2e/prepare_database.py
Procedure and limits: docs/staging-deploy.md, section "Pruebas E2E".
"""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass
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

TARGET_DATABASE = "rckt_e2e_test"
CONFIRMATION = "yes-this-instance-is-disposable-and-exclusive"
DISPOSABLE_MARKER = "rckt-e2e-disposable"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
URL_VARIABLES = (
    "E2E_ADMIN_DATABASE_URL",
    "E2E_MIGRATION_DATABASE_URL",
    "E2E_OPERATOR_DATABASE_URL",
    "E2E_APP_DATABASE_URL",
)
MIGRATOR_ROLE = "rckt_migrator"
SYSTEM_DATABASES = {"postgres", "template0", "template1"}


@dataclass(frozen=True)
class InstanceState:
    """What the server reports before any change. Read-only."""

    other_databases: tuple[str, ...]
    target_exists: bool
    target_marker: str | None


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


def refusal_reasons(urls: dict[str, str], confirmation: str, state: InstanceState | None) -> list[str]:
    """Every reason to refuse, or an empty list. Pure: it touches no database. Without `state` it checks only the
    static conditions, so nothing is connected to until those pass."""
    reasons: list[str] = []
    if confirmation != CONFIRMATION:
        reasons.append("E2E_DISPOSABLE_INSTANCE is not set to the confirmation value")
    locations = set()
    for name in URL_VARIABLES:
        url = urls.get(name, "")
        if not url:
            reasons.append(f"{name} is not set")
            continue
        if not url.startswith("postgresql+asyncpg://"):
            reasons.append(f"{name} is not a postgresql+asyncpg:// URL")
        parts = urlsplit(url)
        if parts.hostname not in LOCAL_HOSTS:
            reasons.append(f"{name} does not point to a local host")
        if database_name(url) != TARGET_DATABASE:
            reasons.append(f"{name} does not name the database {TARGET_DATABASE}")
        locations.add((parts.hostname, parts.port))
    if len(locations) > 1:
        reasons.append("the URLs do not name the same instance")
    if state is not None:
        if state.other_databases:
            reasons.append("the instance holds other user databases, so it is not exclusive to the tests")
        if state.target_exists and state.target_marker != DISPOSABLE_MARKER:
            reasons.append(f"the existing {TARGET_DATABASE} has no disposable marker; refusing to drop it")
    return reasons


async def read_state(admin: str) -> InstanceState:
    """Read-only: lists user databases and the marker of the target. Connects to the maintenance database."""
    engine = create_async_engine(admin_url(admin), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT datname, shobj_description(oid, 'pg_database') FROM pg_database "
                        "WHERE NOT datistemplate AND datname <> 'postgres'"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    others = tuple(sorted(name for name, _ in rows if name != TARGET_DATABASE))
    target = next(((name, marker) for name, marker in rows if name == TARGET_DATABASE), None)
    return InstanceState(
        other_databases=others,
        target_exists=target is not None,
        target_marker=None if target is None else target[1],
    )


async def recreate_database(admin: str) -> None:
    """Drops and recreates the test database, owned by the migrator role (which must already exist: see
    scripts/db_roles/provision_roles.sql), then marks it disposable. Called only after refusal_reasons is empty."""
    engine = create_async_engine(admin_url(admin), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{TARGET_DATABASE}" WITH (FORCE)'))
            await connection.execute(text(f'CREATE DATABASE "{TARGET_DATABASE}" OWNER "{MIGRATOR_ROLE}"'))
            await connection.execute(text(f"COMMENT ON DATABASE \"{TARGET_DATABASE}\" IS '{DISPOSABLE_MARKER}'"))
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


def main() -> None:
    urls = {name: configured(name) for name in URL_VARIABLES}
    confirmation = configured("E2E_DISPOSABLE_INSTANCE")
    # Static checks first: no connection is opened while they fail.
    reasons = refusal_reasons(urls, confirmation, None)
    if reasons:
        raise SystemExit("Refusing to run: " + "; ".join(reasons))
    state = asyncio.run(read_state(urls["E2E_ADMIN_DATABASE_URL"]))
    reasons = refusal_reasons(urls, confirmation, state)
    if reasons:
        raise SystemExit("Refusing to run: " + "; ".join(reasons))
    asyncio.run(recreate_database(urls["E2E_ADMIN_DATABASE_URL"]))
    migrate(urls["E2E_MIGRATION_DATABASE_URL"])
    seed(urls["E2E_OPERATOR_DATABASE_URL"])
    print(f"end-to-end database '{TARGET_DATABASE}' prepared. The backend runs with E2E_APP_DATABASE_URL (rckt_app).")


if __name__ == "__main__":
    main()
