"""Append-only audit trail of the operations that change organization data or read usage.

Revision ID: 0008_audit_events
Revises: 0007_provider_cost
Create Date: 2026-10-27

Equivalent SQL: supabase/migrations/20261027000700_audit_events.sql. The STATEMENTS below are generated from that
file by scripts/generate_alembic_migration.py 0008; edit the SQL and regenerate, never this block by hand.

Statements are executed one at a time: the async driver does not accept several statements in one call.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_audit_events"
down_revision: str | None = "0007_provider_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- BEGIN GENERATED STATEMENTS ---
STATEMENTS = (
    """
    CREATE TABLE public.audit_events (
      id bigint GENERATED ALWAYS AS IDENTITY,
      organization_id uuid NOT NULL,
      occurred_at timestamptz NOT NULL DEFAULT now(),
      source varchar(16) NOT NULL,
      actor_user_id uuid,
      action varchar(48) NOT NULL,
      resource_type varchar(48) NOT NULL,
      resource_id varchar(128) NOT NULL,
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      CONSTRAINT pk_audit_events PRIMARY KEY (id),
      CONSTRAINT fk_audit_events_organization_id
        FOREIGN KEY (organization_id) REFERENCES public.organizations (id) ON DELETE RESTRICT,
      CONSTRAINT fk_audit_events_actor_user_id
        FOREIGN KEY (actor_user_id) REFERENCES public.app_users (id) ON DELETE RESTRICT,
      CONSTRAINT ck_audit_events_source_valid CHECK (source IN ('user', 'internal', 'operator_cli')),
      CONSTRAINT ck_audit_events_actor_matches_source CHECK ((source = 'user') = (actor_user_id IS NOT NULL)),
      CONSTRAINT ck_audit_events_action_valid CHECK (action IN (
        'analysis.created', 'analysis.succeeded', 'analysis.failed',
        'quota.updated', 'membership.granted', 'usage.report_accessed'
      )),
      CONSTRAINT ck_audit_events_resource_type_valid CHECK (resource_type IN (
        'analysis', 'analysis_quota', 'organization_membership', 'usage_report'
      )),
      CONSTRAINT ck_audit_events_resource_id_not_blank CHECK (length(btrim(resource_id)) > 0),
      CONSTRAINT ck_audit_events_metadata_is_object CHECK (jsonb_typeof(metadata) = 'object'),
      CONSTRAINT ck_audit_events_metadata_size CHECK (octet_length(metadata::text) <= 2048)
    )
    """,
    """
    CREATE INDEX ix_audit_events_organization_id_id ON public.audit_events (organization_id, id DESC)
    """,
    """
    CREATE FUNCTION public.audit_events_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP;
    END;
    $$
    """,
    """
    CREATE TRIGGER trg_audit_events_append_only
      BEFORE UPDATE OR DELETE ON public.audit_events
      FOR EACH ROW EXECUTE FUNCTION public.audit_events_append_only()
    """,
    """
    ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY
    """,
)
# --- END GENERATED STATEMENTS ---


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_audit_events_append_only ON public.audit_events")
    op.execute("DROP FUNCTION public.audit_events_append_only()")
    op.execute("DROP TABLE public.audit_events")
