"""Campaigns and append-only campaign metric entries.

Revision ID: 0003_campaigns
Revises: 0002_constraint_names_and_rls
Create Date: 2026-10-25

Equivalent SQL: supabase/migrations/20261025000200_campaigns.sql (must stay structurally identical).

Decisions:
- Money uses numeric (never float): investment and revenue with 2 decimals, unit costs (CPC, CPL) with 4.
- Counts use bigint. The API layer must reject fractional counts before they reach the database.
- A metric entry cannot reference a campaign of another organization (composite foreign key).
- metric entries are append-only: a trigger rejects UPDATE and DELETE. TRUNCATE is not blocked (owner-only).
- Row level security is enabled without policies.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_campaigns"
down_revision: str | None = "0002_constraint_names_and_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NON_NEGATIVE = (
    "(investment IS NULL OR investment >= 0) AND (impressions IS NULL OR impressions >= 0)"
    " AND (reach IS NULL OR reach >= 0) AND (frequency IS NULL OR frequency >= 0)"
    " AND (clicks IS NULL OR clicks >= 0) AND (ctr IS NULL OR ctr >= 0) AND (cpc IS NULL OR cpc >= 0)"
    " AND (leads IS NULL OR leads >= 0) AND (cpl IS NULL OR cpl >= 0)"
    " AND (conversions IS NULL OR conversions >= 0) AND (revenue IS NULL OR revenue >= 0)"
    " AND (roas IS NULL OR roas >= 0)"
)


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Uuid(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=2000), nullable=False),
        sa.Column("channel", sa.String(length=2000), nullable=True),
        sa.Column("objective", sa.String(length=2000), nullable=True),
        sa.Column("period_label", sa.String(length=2000), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), server_default="COP", nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_campaigns"),
        sa.UniqueConstraint("organization_id", "id", name="uq_campaigns_organization_id_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_campaigns_organization_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["app_users.id"], name="fk_campaigns_created_by", ondelete="RESTRICT"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        sa.CheckConstraint(
            "period_start IS NULL OR period_end IS NULL OR period_end >= period_start",
            name="period_order",
        ),
    )
    op.execute(
        "CREATE INDEX ix_campaigns_organization_id_created_at ON public.campaigns (organization_id, created_at DESC)"
    )

    op.create_table(
        "campaign_metric_entries",
        sa.Column("id", sa.Uuid(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("raw_input", sa.Text(), nullable=False),
        sa.Column("period_label", sa.String(length=2000), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("investment", sa.Numeric(14, 2), nullable=True),
        sa.Column("impressions", sa.BigInteger(), nullable=True),
        sa.Column("reach", sa.BigInteger(), nullable=True),
        sa.Column("frequency", sa.Numeric(12, 4), nullable=True),
        sa.Column("clicks", sa.BigInteger(), nullable=True),
        sa.Column("ctr", sa.Numeric(9, 4), nullable=True),
        sa.Column("cpc", sa.Numeric(14, 4), nullable=True),
        sa.Column("leads", sa.BigInteger(), nullable=True),
        sa.Column("cpl", sa.Numeric(14, 4), nullable=True),
        sa.Column("conversions", sa.BigInteger(), nullable=True),
        sa.Column("revenue", sa.Numeric(14, 2), nullable=True),
        sa.Column("roas", sa.Numeric(12, 4), nullable=True),
        sa.Column("previous_metrics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_metric_entries"),
        sa.ForeignKeyConstraint(
            ["organization_id", "campaign_id"],
            ["campaigns.organization_id", "campaigns.id"],
            name="fk_campaign_metric_entries_campaign_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["app_users.id"], name="fk_campaign_metric_entries_created_by", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("source IN ('form', 'text')", name="source_valid"),
        sa.CheckConstraint("char_length(raw_input) <= 12000", name="raw_input_length"),
        sa.CheckConstraint(
            "period_start IS NULL OR period_end IS NULL OR period_end >= period_start",
            name="period_order",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(previous_metrics) = 'object'", name="previous_is_object"
        ),
        sa.CheckConstraint(NON_NEGATIVE, name="non_negative"),
    )
    op.execute(
        "CREATE INDEX ix_campaign_metric_entries_campaign_created"
        " ON public.campaign_metric_entries (organization_id, campaign_id, created_at DESC)"
    )
    op.execute(
        """
        CREATE FUNCTION public.campaign_metric_entries_reject_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'campaign_metric_entries is append-only: % is not allowed', TG_OP;
END;
$$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_campaign_metric_entries_append_only"
        " BEFORE UPDATE OR DELETE ON public.campaign_metric_entries"
        " FOR EACH ROW EXECUTE FUNCTION public.campaign_metric_entries_reject_change()"
    )

    op.execute("ALTER TABLE public.campaigns ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.campaign_metric_entries ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_campaign_metric_entries_append_only ON public.campaign_metric_entries")
    op.execute("DROP FUNCTION public.campaign_metric_entries_reject_change()")
    op.drop_table("campaign_metric_entries")
    op.drop_table("campaigns")
