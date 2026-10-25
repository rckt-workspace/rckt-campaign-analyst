"""Constraint names that match the models, and row level security on exposed tables.

Revision ID: 0002_constraint_names_and_rls
Revises: 0001_foundation
Create Date: 2026-10-25

Why:
- Migration 0001 applied the naming convention twice to its CHECK constraints, producing names such as
  ck_organizations_ck_organizations_slug_format, and a truncated name for the membership role check.
  The models expect ck_organizations_slug_format, ck_organizations_status_valid and
  ck_organization_memberships_role_valid. 0001 is not edited (it may already be applied); this revision renames.
- Row level security is enabled without policies. Roles that the managed APIs expose (anon, authenticated) get no
  rows by default. The owner role used by FastAPI bypasses RLS (ENABLE, not FORCE). Policies are added later,
  once identity integration is verified (see supabase/README.md).

Equivalent SQL for new databases: supabase/migrations/20261025000100_foundation.sql.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_constraint_names_and_rls"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMES = (
    ("organizations", "ck_organizations_ck_organizations_slug_format", "ck_organizations_slug_format"),
    ("organizations", "ck_organizations_ck_organizations_status_valid", "ck_organizations_status_valid"),
    (
        "organization_memberships",
        "ck_organization_memberships_ck_organization_memberships_7648",
        "ck_organization_memberships_role_valid",
    ),
)
TABLES = ("organizations", "app_users", "organization_memberships")


def upgrade() -> None:
    for table, old, new in RENAMES:
        op.execute(f"ALTER TABLE public.{table} RENAME CONSTRAINT {old} TO {new}")
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
    for table, old, new in RENAMES:
        op.execute(f"ALTER TABLE public.{table} RENAME CONSTRAINT {new} TO {old}")
