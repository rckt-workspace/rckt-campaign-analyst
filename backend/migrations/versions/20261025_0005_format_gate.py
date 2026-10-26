"""A succeeded analysis must follow the required report format.

Revision ID: 0005_format_gate
Revises: 0004_analyses
Create Date: 2026-10-26

Equivalent SQL: supabase/migrations/20261025000400_format_gate.sql (must stay structurally identical).

Decision: an answer that breaks the required format after the allowed retry is stored as 'failed' with
AI_INVALID_RESPONSE, never as 'succeeded'. Only the constraint changes; no rows are rewritten.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_format_gate"
down_revision: str | None = "0004_analyses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUCCEEDED_STATE_STRICT = (
    "status <> 'succeeded'"
    " OR (completed_at IS NOT NULL AND lease_expires_at IS NULL AND error_code IS NULL"
    " AND analysis_markdown IS NOT NULL AND length(btrim(analysis_markdown)) > 0"
    " AND model_used IS NOT NULL AND format_valid IS TRUE)"
)
SUCCEEDED_STATE_PREVIOUS = (
    "status <> 'succeeded'"
    " OR (completed_at IS NOT NULL AND lease_expires_at IS NULL AND error_code IS NULL"
    " AND analysis_markdown IS NOT NULL AND length(btrim(analysis_markdown)) > 0"
    " AND model_used IS NOT NULL AND format_valid IS NOT NULL)"
)


def upgrade() -> None:
    op.execute("ALTER TABLE public.analyses DROP CONSTRAINT ck_analyses_succeeded_state")
    op.execute(f"ALTER TABLE public.analyses ADD CONSTRAINT ck_analyses_succeeded_state CHECK ({SUCCEEDED_STATE_STRICT})")


def downgrade() -> None:
    op.execute("ALTER TABLE public.analyses DROP CONSTRAINT ck_analyses_succeeded_state")
    op.execute(f"ALTER TABLE public.analyses ADD CONSTRAINT ck_analyses_succeeded_state CHECK ({SUCCEEDED_STATE_PREVIOUS})")
