"""Reproducible verification of Phase 2.7B2 on a disposable PostgreSQL instance. Nothing else is touched.

What it does, in order (each step stops the run on failure):
  1. Guard: proves the target is the container this phase created, and only that one (see verify_container).
  2. Provisions the roles with scripts/db_roles/provision_roles.sql (cluster-wide: allowed only by step 1).
  3. Recreates the databases it uses, all named rckt_2_7b2_* and ending in _test, inside the verified container.
  4. Applies migrations 0001-0009 with Alembic as rckt_migrator, and the portable SQL chain to a second database.
  5. Compares both schemas with pg_dump (alembic_version excluded, line formatting normalized).
  6. Downgrades to 0008 and compares the result with the portable 0001-0008 chain; upgrades back to head.
  7. Runs the test suite against the instance, unless --skip-tests is given.

It never removes a container or a volume, never reads a credential, and prints no connection string or secret.

Usage (repository root, venv Python):
  set POC_CONTAINER=rckt-sec-2-7b2-pg
  set POC_CONTAINER_ID=<full id printed by: docker inspect -f {{.Id}} rckt-sec-2-7b2-pg>
  set POC_PORT=56413
  python scripts/security_poc/run_rls_suite.py [--migrations-only | --skip-tests]
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
ROLES_SQL = REPO / "scripts" / "db_roles" / "provision_roles.sql"
MIGRATIONS = REPO / "supabase" / "migrations"
PURPOSE_LABEL = "rckt.purpose"
PURPOSE_VALUE = "db-security-2.7b2"
MAIN_DB = "rckt_2_7b2_test"
SQLCHECK_DB = "rckt_2_7b2_sqlcheck_test"
REFERENCE_0008_DB = "rckt_2_7b2_0008_test"
TEST_DATABASES = (MAIN_DB, SQLCHECK_DB, REFERENCE_0008_DB)
PORTABLE_FILES_BEFORE_0009 = sorted(p for p in MIGRATIONS.glob("*.sql") if not p.name.startswith("20261029"))
PORTABLE_ALL = sorted(MIGRATIONS.glob("*.sql"))


class Refusal(SystemExit):
    """The guard refused. Nothing was changed."""


def docker(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"docker {args[0]} failed (exit {result.returncode}); no further steps were run")
    return result.stdout


def verify_container(name: str, container_id: str, port: str) -> None:
    """Refuses unless the container is the one this phase created, and the only one publishing the port.

    Checks: the name and the full id match what the operator pinned; the purpose label is set; the image is PostgreSQL 16;
    the port is published on 127.0.0.1 only; the container is not managed by Compose; no other running container publishes
    the same host port. A TCP port cannot prove the process behind it, so the pinned id and the single-publisher rule are the
    guard's limit, and it is stated in the phase document.
    """
    if not (name and container_id and port.isdigit()):
        raise Refusal("refused: set POC_CONTAINER, POC_CONTAINER_ID and POC_PORT")
    inspected = json.loads(docker("inspect", name))
    if len(inspected) != 1:
        raise Refusal("refused: the container name does not resolve to exactly one container")
    info = inspected[0]
    if not info["Id"].startswith(container_id) or len(container_id) < 12:
        raise Refusal("refused: the container id does not match the pinned POC_CONTAINER_ID")
    labels = info["Config"].get("Labels") or {}
    if labels.get(PURPOSE_LABEL) != PURPOSE_VALUE:
        raise Refusal("refused: the container is not labelled for this phase")
    if "com.docker.compose.project" in labels:
        raise Refusal("refused: the container is managed by Compose: not a disposable instance of this phase")
    if not info["Config"]["Image"].startswith("postgres:16"):
        raise Refusal("refused: the image is not PostgreSQL 16")
    bindings = (info["HostConfig"].get("PortBindings") or {}).get("5432/tcp") or []
    if [b.get("HostIp") for b in bindings] != ["127.0.0.1"] or [b.get("HostPort") for b in bindings] != [port]:
        raise Refusal("refused: the port is not published on 127.0.0.1 with the pinned POC_PORT")
    publishers = docker("ps", "--format", "{{.Names}}\t{{.Ports}}").splitlines()
    others = [line.split("\t")[0] for line in publishers
              if f"127.0.0.1:{port}->" in line and not line.startswith(name + "\t")]
    if others:
        raise Refusal("refused: another container publishes the same port")


def psql(name: str, database: str, sql: str, *, role: str = "postgres") -> None:
    docker("exec", "-i", name, "psql", "-U", role, "-d", database, "-v", "ON_ERROR_STOP=1", "-q", "-f", "-", stdin=sql)


def psql_file(name: str, database: str, path: Path, *, role: str) -> None:
    psql(name, database, path.read_text(encoding="utf-8"), role=role)


def recreate_databases(name: str) -> None:
    for database in TEST_DATABASES:
        if not database.endswith("_test") or not database.startswith("rckt_2_7b2_"):
            raise Refusal("refused: database names are fixed by this script")
        psql(name, "postgres", f"DROP DATABASE IF EXISTS {database};")
        psql(name, "postgres", f"CREATE DATABASE {database} OWNER rckt_migrator;")


# The downgrade cannot delete a row of pg_default_acl (superuser only). Its effect is checked directly below, and these
# catalog lines are excluded from the comparison for that reason only.
DEFAULT_ACL_LINES = ("ALTER DEFAULT PRIVILEGES",)


def normalized_dump(name: str, database: str, ignore: tuple[str, ...] = ()) -> list[str]:
    raw = docker("exec", name, "pg_dump", "-s", "--exclude-table=alembic_version", "-U", "postgres", "-d", database)
    lines = []
    for line in raw.splitlines():
        if re.match(r"^\\(un)?restrict ", line) or line.startswith("-- Dumped") or line.startswith("-- Started") \
                or line.startswith("-- Name") or line.startswith("-- PostgreSQL database dump") or line.strip() in ("", "--"):
            continue
        normalized = " ".join(line.split())  # whitespace-insensitive: the generator indents function bodies
        if not normalized.startswith(ignore):
            lines.append(normalized)
    return lines


def compare(name: str, left: str, right: str, label: str, ignore: tuple[str, ...] = ()) -> None:
    a, b = normalized_dump(name, left, ignore), normalized_dump(name, right, ignore)
    if a != b:
        diff = "\n".join(list(difflib.unified_diff(a, b, lineterm=""))[:60])
        raise SystemExit(f"{label}: schemas differ\n{diff}")
    print(f"ok  {label}: identical schemas ({len(a)} normalized lines)")


def new_function_public_execute(name: str, database: str) -> bool:
    """Whether PUBLIC may execute a function the schema owner creates now. Rolled back: nothing is left behind."""
    statements = [
        "BEGIN;",
        # After a downgrade public belongs to pg_database_owner: the schema owner needs CREATE to run the probe.
        "GRANT CREATE ON SCHEMA public TO rckt_schema_owner;",
        "SET LOCAL ROLE rckt_schema_owner;",
        "CREATE FUNCTION public.zz_default_probe() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;",
        "SELECT has_function_privilege('public', 'public.zz_default_probe()', 'EXECUTE');",
        "ROLLBACK;",
    ]
    sql = "\n".join(statements) + "\n"
    out = docker("exec", "-i", name, "psql", "-U", "rckt_migrator", "-d", database, "-v", "ON_ERROR_STOP=1",
                 "-q", "-t", "-A", "-f", "-", stdin=sql)
    return out.strip().splitlines()[-1] == "t"


def alembic(*args: str) -> None:
    env = dict(os.environ)
    env["DATABASE_MIGRATION_URL"] = f"postgresql+asyncpg://rckt_migrator@127.0.0.1:{PORT}/{MAIN_DB}"
    result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-6:])
        raise SystemExit(f"alembic {' '.join(args)} failed:\n{tail}")
    print(f"ok  alembic {' '.join(args)}")


PORT = ""


def main() -> int:
    global PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--migrations-only", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    options = parser.parse_args()
    name = os.environ.get("POC_CONTAINER", "").strip()
    container_id = os.environ.get("POC_CONTAINER_ID", "").strip()
    PORT = os.environ.get("POC_PORT", "").strip()

    verify_container(name, container_id, PORT)
    print(f"ok  guard: container {name} is the pinned instance of this phase, on 127.0.0.1:{PORT}")

    psql_file(name, "postgres", ROLES_SQL, role="postgres")
    print("ok  roles provisioned (idempotent)")
    recreate_databases(name)
    print("ok  databases recreated: " + ", ".join(TEST_DATABASES))

    alembic("upgrade", "head")
    for path in PORTABLE_ALL:
        psql_file(name, SQLCHECK_DB, path, role="rckt_migrator")
    print("ok  portable SQL chain 0001-0009 applied to the second database")
    compare(name, MAIN_DB, SQLCHECK_DB, "Alembic head vs portable SQL head")

    alembic("downgrade", "0008_audit_events")
    for path in PORTABLE_FILES_BEFORE_0009:
        psql_file(name, REFERENCE_0008_DB, path, role="rckt_migrator")
    compare(name, MAIN_DB, REFERENCE_0008_DB, "downgrade to 0008 vs portable 0001-0008", ignore=DEFAULT_ACL_LINES)
    if not new_function_public_execute(name, MAIN_DB):
        raise SystemExit("FAIL downgrade: PUBLIC does not execute new functions (built-in default expected)")
    print("ok  downgrade: a new function is executable by PUBLIC again (built-in default)")
    alembic("upgrade", "head")
    exposed = docker("exec", "-i", name, "psql", "-U", "postgres", "-d", MAIN_DB, "-tA", "-c",
                     "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                     " WHERE n.nspname = 'public' AND has_function_privilege('public', p.oid, 'EXECUTE')")
    if new_function_public_execute(name, MAIN_DB):
        raise SystemExit("FAIL upgrade: PUBLIC executes a function the schema owner creates")
    print("ok  upgrade: a new function created by the schema owner is not executable by PUBLIC")
    if exposed.strip() != "0":
        raise SystemExit(f"FAIL: {exposed.strip()} function(s) in public are executable by PUBLIC")
    print("ok  no function in public is executable by PUBLIC")

    if options.migrations_only or options.skip_tests:
        print("done (tests not run)")
        return 0
    psql(name, "postgres", """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rckt_operator_test') THEN
            CREATE ROLE rckt_operator_test LOGIN NOSUPERUSER NOBYPASSRLS INHERIT;
        END IF;
        GRANT rckt_operator TO rckt_operator_test;
    END $$;""")
    print("ok  test login of the operator (a member of rckt_operator, test instance only)")
    return run_tests(PORT)


def run_tests(port: str) -> int:
    """The full backend suite, on the instance. Credentials are built in-process from the port; none is printed."""
    env = dict(os.environ)
    base = f"@127.0.0.1:{port}/{MAIN_DB}"
    env["TEST_DATABASE_URL"] = f"postgresql+asyncpg://rckt_migrator{base}"
    env["TEST_OPERATOR_DATABASE_URL"] = f"postgresql+asyncpg://rckt_operator_test{base}"
    env["TEST_APP_DATABASE_URL"] = f"postgresql+asyncpg://rckt_app{base}"
    env["TEST_OWNER_ROLE"] = "rckt_schema_owner"
    for variable in ("DATABASE_URL", "DATABASE_MIGRATION_URL", "DATABASE_OPERATOR_URL"):
        env.pop(variable, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--tb=short", "tests"],
                            cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8")
    print(result.stdout[-6000:])
    print(result.stderr[-1500:], file=sys.stderr)
    print(f"tests exit code: {result.returncode}")
    return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refusal as refusal:
        print(refusal, file=sys.stderr)
        raise SystemExit(2)
