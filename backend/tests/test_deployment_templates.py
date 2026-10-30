"""Static checks of the deployment and CI templates. No parser dependency: plain text rules over the files.

They protect decisions, not syntax: the runtime login is never the platform's default user, migrations never run on
deploy, nothing deploys by itself, every region is the agreed one, and no credential value is written in a template.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RENDER = (REPO / "render.yaml").read_text(encoding="utf-8")
CI = (REPO / ".github" / "workflows" / "docker-ci.yml").read_text(encoding="utf-8")
BACKEND_DOCKERFILE = (REPO / "backend" / "Dockerfile").read_text(encoding="utf-8")

# A URL with an inline password (user:secret@) is a credential. Placeholders such as <PASSWORD> are not.
INLINE_CREDENTIAL = re.compile(r"://[^/\s:@'\"]+:(?!<)[^@\s'\"<]+@")


def test_the_runtime_database_login_is_set_by_hand_never_taken_from_the_platform_database():
    assert "fromDatabase" not in RENDER
    block = RENDER.split("- key: DATABASE_URL", 1)[1].split("- key:", 1)[0]
    assert "sync: false" in block


def test_migrations_never_run_on_deploy():
    assert not re.search(r"^\s*preDeployCommand:", RENDER, flags=re.M)
    assert not re.search(r"^[^#\n]*alembic", RENDER, flags=re.M)


def test_nothing_deploys_by_itself():
    assert RENDER.count("autoDeploy: false") == RENDER.count("runtime: docker") == 2


def test_every_service_and_database_is_in_the_agreed_region():
    regions = re.findall(r"region: (\S+)", RENDER)
    assert regions and set(regions) == {"virginia"}


def test_the_private_api_address_comes_from_the_private_network_not_a_typed_url():
    assert "property: hostport" in RENDER
    assert "type: pserv" in RENDER
    assert "FASTAPI_INTERNAL_URL" in RENDER
    assert "http://" not in RENDER.split("FASTAPI_INTERNAL_URL", 1)[1].split("- key:", 1)[0]


def test_no_template_contains_an_inline_credential():
    for name, text in {"render.yaml": RENDER, "docker-ci.yml": CI}.items():
        for match in INLINE_CREDENTIAL.finditer(text):
            # Only the throwaway CI service password is allowed, and only inside the CI workflow.
            assert name == "docker-ci.yml" and "ci-only-password" in match.group(0), (name, match.group(0)[:20])


def test_the_e2e_job_never_uses_the_retired_single_url_and_confirms_a_disposable_instance():
    assert "E2E_DATABASE_URL" not in CI
    assert "E2E_DISPOSABLE_INSTANCE: yes-this-instance-is-disposable-and-exclusive" in CI
    for variable in ("E2E_ADMIN_DATABASE_URL", "E2E_MIGRATION_DATABASE_URL", "E2E_OPERATOR_DATABASE_URL", "E2E_APP_DATABASE_URL"):
        assert variable in CI


def test_every_e2e_url_names_only_the_test_database():
    e2e_urls = re.findall(r"E2E_\w+_DATABASE_URL: \S+/(\w+)$", CI, flags=re.M)
    assert len(e2e_urls) == 4 and set(e2e_urls) == {"rckt_e2e_test"}


def test_the_api_listens_on_the_platform_port_and_falls_back_to_8000():
    assert '--port \\"${PORT:-8000}\\"' in BACKEND_DOCKERFILE or '--port \"${PORT:-8000}\"' in BACKEND_DOCKERFILE
    assert "os.environ.get('PORT', '8000')" in BACKEND_DOCKERFILE
