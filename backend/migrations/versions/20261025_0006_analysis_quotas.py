"""Analysis quotas per organization and UTC month, and the counted month of each analysis.

Revision ID: 0006_analysis_quotas
Revises: 0005_format_gate
Create Date: 2026-10-26

Equivalent SQL: supabase/migrations/20261025000500_analysis_quotas.sql. The STATEMENTS below are generated from that
file by scripts/generate_alembic_migration.py 0006; edit the SQL and regenerate, never this block by hand.

Statements are executed one at a time: the async driver does not accept several statements in one call.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_analysis_quotas"
down_revision: str | None = "0005_format_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- BEGIN GENERATED STATEMENTS ---
STATEMENTS = (
    """
    CREATE TABLE public.analysis_quotas (
      organization_id uuid NOT NULL,
      plan varchar(16) NOT NULL,
      monthly_analysis_limit integer NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT pk_analysis_quotas PRIMARY KEY (organization_id),
      CONSTRAINT fk_analysis_quotas_organization_id
        FOREIGN KEY (organization_id) REFERENCES public.organizations (id) ON DELETE RESTRICT,
      CONSTRAINT ck_analysis_quotas_plan_valid CHECK (plan IN ('pilot', 'standard', 'enterprise')),
      CONSTRAINT ck_analysis_quotas_limit_non_negative CHECK (monthly_analysis_limit >= 0),
      CONSTRAINT ck_analysis_quotas_updated_after_created CHECK (updated_at >= created_at)
    )
    """,
    """
    CREATE TABLE public.analysis_quota_usage (
      organization_id uuid NOT NULL,
      period_start date NOT NULL,
      consumed integer NOT NULL DEFAULT 0,
      released integer NOT NULL DEFAULT 0,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT pk_analysis_quota_usage PRIMARY KEY (organization_id, period_start),
      CONSTRAINT fk_analysis_quota_usage_organization_id
        FOREIGN KEY (organization_id) REFERENCES public.organizations (id) ON DELETE RESTRICT,
      CONSTRAINT ck_analysis_quota_usage_first_day CHECK (extract(day from period_start) = 1),
      CONSTRAINT ck_analysis_quota_usage_consumed_non_negative CHECK (consumed >= 0),
      CONSTRAINT ck_analysis_quota_usage_released_non_negative CHECK (released >= 0)
    )
    """,
    """
    ALTER TABLE public.analyses ADD COLUMN quota_period_start date
    """,
    """
    ALTER TABLE public.analyses
      ADD CONSTRAINT ck_analyses_quota_period_first_day
      CHECK (quota_period_start IS NULL OR extract(day from quota_period_start) = 1)
    """,
    """
    ALTER TABLE public.analyses
      ADD CONSTRAINT fk_analyses_quota_usage
      FOREIGN KEY (organization_id, quota_period_start)
      REFERENCES public.analysis_quota_usage (organization_id, period_start) ON DELETE RESTRICT
    """,
    """
    ALTER TABLE public.analysis_quotas ENABLE ROW LEVEL SECURITY
    """,
    """
    ALTER TABLE public.analysis_quota_usage ENABLE ROW LEVEL SECURITY
    """,
)
# --- END GENERATED STATEMENTS ---


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE public.analyses DROP CONSTRAINT fk_analyses_quota_usage")
    op.execute("ALTER TABLE public.analyses DROP CONSTRAINT ck_analyses_quota_period_first_day")
    op.execute("ALTER TABLE public.analyses DROP COLUMN quota_period_start")
    op.execute("DROP TABLE public.analysis_quota_usage")
    op.execute("DROP TABLE public.analysis_quotas")
