"""Guard: every configuration variable the backend reads is documented in .env.example, and the service-role client is not used.

No database, no network. The scan reads source files only; no value of any variable is loaded or printed.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PREFIXES = ("DATABASE_", "SUPABASE_", "OPENROUTER_", "CHAT_", "ANALYSIS_QUOTA_", "APP_", "FASTAPI_", "POSTGRES_", "E2E_")
NAME = re.compile(r"[\"']((?:" + "|".join(PREFIXES) + r")[A-Z0-9_]+)[\"']")


def _documented_names() -> set[str]:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, flags=re.M))


def _names_read_by_backend() -> set[str]:
    names: set[str] = set()
    for path in (REPO / "backend" / "app").rglob("*.py"):
        names.update(NAME.findall(path.read_text(encoding="utf-8")))
    return names


def test_every_variable_the_backend_reads_is_documented_in_env_example():
    missing = sorted(_names_read_by_backend() - _documented_names())
    assert missing == [], f"read by the backend but absent from .env.example (names only): {missing}"


def test_the_service_role_key_is_read_only_by_the_generated_client_and_never_imported():
    readers = [
        path.relative_to(REPO).as_posix()
        for path in (REPO / "src").rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx"} and "SUPABASE_SERVICE_ROLE_KEY" in path.read_text(encoding="utf-8")
    ]
    assert readers == ["src/integrations/supabase/client.server.ts"], readers
    importers = [
        path.relative_to(REPO).as_posix()
        for path in (REPO / "src").rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx"}
        and any(
            "integrations/supabase/client.server" in line and not line.strip().startswith("//")
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    ]
    assert importers == [], f"the service-role client must not be imported: {importers}"
