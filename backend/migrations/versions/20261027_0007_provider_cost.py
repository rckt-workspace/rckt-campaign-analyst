"""Exact provider cost of each analysis call, as OpenRouter reports it.

Revision ID: 0007_provider_cost
Revises: 0006_analysis_quotas
Create Date: 2026-10-27

Equivalent SQL: supabase/migrations/20261027000600_provider_cost.sql. The STATEMENTS below are generated from that
file by scripts/generate_alembic_migration.py 0007; edit the SQL and regenerate, never this block by hand.

Statements are executed one at a time: the async driver does not accept several statements in one call.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_provider_cost"
down_revision: str | None = "0006_analysis_quotas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- BEGIN GENERATED STATEMENTS ---
STATEMENTS = (
    """
    ALTER TABLE public.analysis_usage ADD COLUMN provider_cost numeric(18, 8)
    """,
    """
    ALTER TABLE public.analysis_usage
      ADD CONSTRAINT ck_analysis_usage_provider_cost_non_negative
      CHECK (provider_cost IS NULL OR provider_cost >= 0)
    """,
    """
    ALTER TABLE public.analysis_usage
      ADD CONSTRAINT ck_analysis_usage_provider_cost_reported
      CHECK (provider_cost IS NULL OR usage_reported)
    """,
)
# --- END GENERATED STATEMENTS ---


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE public.analysis_usage DROP CONSTRAINT ck_analysis_usage_provider_cost_reported")
    op.execute("ALTER TABLE public.analysis_usage DROP CONSTRAINT ck_analysis_usage_provider_cost_non_negative")
    op.execute("ALTER TABLE public.analysis_usage DROP COLUMN provider_cost")
