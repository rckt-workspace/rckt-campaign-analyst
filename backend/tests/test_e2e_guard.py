"""Guard of e2e/prepare_database.py: the only destructive step of the end-to-end suite is refused unless every condition
holds. Pure: no database, no network. Only names of variables and reasons are compared; no URL is printed."""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("prepare_database", REPO / "e2e" / "prepare_database.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

LOCAL = "127.0.0.1:55432"


def urls(host: str = LOCAL, database: str = "rckt_e2e_test") -> dict[str, str]:
    base = f"{host}/{database}"
    return {
        "E2E_ADMIN_DATABASE_URL": f"postgresql+asyncpg://admin:x@{base}",
        "E2E_MIGRATION_DATABASE_URL": f"postgresql+asyncpg://rckt_migrator:x@{base}",
        "E2E_OPERATOR_DATABASE_URL": f"postgresql+asyncpg://operator:x@{base}",
        "E2E_APP_DATABASE_URL": f"postgresql+asyncpg://rckt_app:x@{base}",
    }


CONFIRMED = guard.CONFIRMATION
EMPTY = guard.InstanceState(other_databases=(), target_exists=False, target_marker=None)
MARKED = guard.InstanceState(other_databases=(), target_exists=True, target_marker=guard.DISPOSABLE_MARKER)


def test_a_fresh_exclusive_local_instance_passes_every_check():
    assert guard.refusal_reasons(urls(), CONFIRMED, EMPTY) == []


def test_a_previous_run_with_the_marker_passes_so_the_database_can_be_recreated():
    assert guard.refusal_reasons(urls(), CONFIRMED, MARKED) == []


def test_nothing_is_dropped_without_the_explicit_confirmation():
    reasons = guard.refusal_reasons(urls(), "", EMPTY)
    assert any("E2E_DISPOSABLE_INSTANCE" in reason for reason in reasons)


def test_a_suffix_is_not_enough_the_name_must_be_exact():
    for name in ("rckt_staging_test", "rckt_e2e_test_old", "rckt_e2e"):
        reasons = guard.refusal_reasons(urls(database=name), CONFIRMED, EMPTY)
        assert any("does not name the database" in reason for reason in reasons), name


def test_a_remote_host_is_refused_even_with_the_right_name():
    reasons = guard.refusal_reasons(urls(host="db.example.test:5432"), CONFIRMED, EMPTY)
    assert any("does not point to a local host" in reason for reason in reasons)


def test_the_four_urls_must_name_the_same_instance():
    mixed = urls()
    mixed["E2E_APP_DATABASE_URL"] = "postgresql+asyncpg://rckt_app:x@127.0.0.1:5433/rckt_e2e_test"
    assert any("same instance" in reason for reason in guard.refusal_reasons(mixed, CONFIRMED, EMPTY))


def test_a_shared_instance_with_other_user_databases_is_refused():
    shared = guard.InstanceState(other_databases=("rckt",), target_exists=False, target_marker=None)
    reasons = guard.refusal_reasons(urls(), CONFIRMED, shared)
    assert any("other user databases" in reason for reason in reasons)


def test_an_existing_test_database_without_the_marker_is_never_dropped():
    unmarked = guard.InstanceState(other_databases=(), target_exists=True, target_marker=None)
    reasons = guard.refusal_reasons(urls(), CONFIRMED, unmarked)
    assert any("no disposable marker" in reason for reason in reasons)


def test_a_missing_url_is_reported_by_variable_name_only():
    partial = urls()
    del partial["E2E_OPERATOR_DATABASE_URL"]
    reasons = guard.refusal_reasons(partial, CONFIRMED, None)
    assert "E2E_OPERATOR_DATABASE_URL is not set" in reasons


def test_static_checks_refuse_before_any_connection_is_opened():
    reasons = guard.refusal_reasons(urls(host="db.example.test:5432"), "", None)
    assert any("E2E_DISPOSABLE_INSTANCE" in reason for reason in reasons)
    assert any("does not point to a local host" in reason for reason in reasons)
    assert not any("other user databases" in reason for reason in reasons)


@pytest.mark.parametrize("url", ["postgresql://admin:x@127.0.0.1/rckt_e2e_test", "mysql://x@127.0.0.1/rckt_e2e_test"])
def test_only_the_asyncpg_driver_is_accepted(url):
    values = urls()
    values["E2E_ADMIN_DATABASE_URL"] = url
    assert any("postgresql+asyncpg://" in reason for reason in guard.refusal_reasons(values, CONFIRMED, EMPTY))
