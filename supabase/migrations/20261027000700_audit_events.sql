-- RCKT Campaign Analyst: append-only audit trail of the operations that change organization data or read usage.
--
-- Incremental migration. Apply after 20261027000600_provider_cost.sql, once per database. Do not edit it after it runs.
-- Equivalent to Alembic revision 0008_audit_events in backend/migrations (must stay structurally identical).
-- Portable: plain PostgreSQL 13 or newer. No extensions, no project identifiers, no IF NOT EXISTS.
--
-- Rules enforced here, not only in application code:
-- - Rows are never updated or deleted: a BEFORE UPDATE OR DELETE trigger refuses both.
-- - Every row has an organization, a stable action code, a resource, and a source (user, internal process, or operator CLI).
--   A user source needs an actor; the other sources have none. No actor is invented for a CLI action.
-- - occurred_at is set by PostgreSQL, never by the application.
-- - metadata is a JSON object of bounded size. Which keys each action may carry is decided by the application's explicit
--   allow-list (app/domain/audit.py), which refuses anything else before it reaches this table.
-- Row level security is ON with no policies and no grants to anon or authenticated, as in the previous migrations.
-- TRUNCATE is not blocked by a row trigger, as with the analysis tables: the application role must not hold it
-- (docs/PHASE2_AUDIT.md).

BEGIN;

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
);

-- Cursor reads: newest first, per organization.
CREATE INDEX ix_audit_events_organization_id_id ON public.audit_events (organization_id, id DESC);

CREATE FUNCTION public.audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP;
END;
$$;

CREATE TRIGGER trg_audit_events_append_only
  BEFORE UPDATE OR DELETE ON public.audit_events
  FOR EACH ROW EXECUTE FUNCTION public.audit_events_append_only();

-- Closed to the managed APIs until identity is verified: RLS on, no policies, no grants.
ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY;

COMMIT;
