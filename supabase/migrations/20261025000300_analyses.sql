-- RCKT Campaign Analyst: persisted AI analyses and the usage of each provider attempt.
--
-- Incremental migration. Apply after 20261025000200_campaigns.sql, once per database. Do not edit it after it runs.
-- Equivalent to Alembic revision 0004_analyses in backend/migrations (must stay structurally identical).
-- Portable: plain PostgreSQL 13 or newer. No extensions, no project identifiers, no IF NOT EXISTS.
--
-- Isolation: every row carries organization_id, and every foreign key includes it. An analysis cannot point at a
-- campaign, metric entry or attempt of another organization.
-- Idempotency: UNIQUE (organization_id, idempotency_key). A claim is an INSERT ... ON CONFLICT DO NOTHING, so
-- concurrent requests with one key produce one row and one provider call.
-- History: completed analyses and recorded attempts cannot be changed or deleted (triggers below).
-- Usage: tokens and cost are stored only when the provider reports them (usage_reported). Never estimated here.
-- Row level security is ON with no policies, as in the previous migrations.

BEGIN;

-- A metric entry is referenced together with its campaign, so it can only belong to that campaign.
ALTER TABLE public.campaign_metric_entries
  ADD CONSTRAINT uq_campaign_metric_entries_organization_campaign_id
  UNIQUE (organization_id, campaign_id, id);

-- One row per analysis request. status 'running' holds a lease; the lease expiring never re-enables a provider call.
CREATE TABLE public.analyses (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  campaign_id uuid,
  metric_entry_id uuid,
  requested_by uuid NOT NULL,
  idempotency_key varchar(128) NOT NULL,
  input_hash char(64) NOT NULL,
  status varchar(16) NOT NULL,
  lease_expires_at timestamptz,
  error_code varchar(64),
  deterministic_report jsonb,
  analysis_markdown text,
  prompt_version varchar(64) NOT NULL,
  model_requested varchar(200) NOT NULL,
  model_used varchar(200),
  used_fallback boolean NOT NULL DEFAULT false,
  latency_ms integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT pk_analyses PRIMARY KEY (id),
  CONSTRAINT uq_analyses_organization_id_id UNIQUE (organization_id, id),
  CONSTRAINT uq_analyses_organization_id_idempotency_key UNIQUE (organization_id, idempotency_key),
  CONSTRAINT fk_analyses_organization_id
    FOREIGN KEY (organization_id) REFERENCES public.organizations (id) ON DELETE RESTRICT,
  CONSTRAINT fk_analyses_campaign
    FOREIGN KEY (organization_id, campaign_id) REFERENCES public.campaigns (organization_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_analyses_metric_entry
    FOREIGN KEY (organization_id, campaign_id, metric_entry_id)
    REFERENCES public.campaign_metric_entries (organization_id, campaign_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_analyses_requested_by
    FOREIGN KEY (requested_by) REFERENCES public.app_users (id) ON DELETE RESTRICT,
  CONSTRAINT ck_analyses_status_valid CHECK (status IN ('running', 'succeeded', 'failed')),
  CONSTRAINT ck_analyses_idempotency_key_not_blank CHECK (length(btrim(idempotency_key)) > 0),
  CONSTRAINT ck_analyses_input_hash_format CHECK (input_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_analyses_prompt_version_not_blank CHECK (length(btrim(prompt_version)) > 0),
  CONSTRAINT ck_analyses_model_requested_not_blank CHECK (length(btrim(model_requested)) > 0),
  CONSTRAINT ck_analyses_metric_entry_needs_campaign CHECK (metric_entry_id IS NULL OR campaign_id IS NOT NULL),
  CONSTRAINT ck_analyses_error_code_valid CHECK (
    error_code IS NULL OR error_code IN (
      'AI_TIMEOUT', 'AI_UNAVAILABLE', 'AI_INVALID_RESPONSE', 'AI_NOT_CONFIGURED', 'ANALYSIS_ABANDONED', 'INTERNAL_ERROR'
    )
  ),
  CONSTRAINT ck_analyses_running_state CHECK (
    status <> 'running'
    OR (lease_expires_at IS NOT NULL AND completed_at IS NULL AND analysis_markdown IS NULL AND error_code IS NULL)
  ),
  CONSTRAINT ck_analyses_succeeded_state CHECK (
    status <> 'succeeded'
    OR (
      completed_at IS NOT NULL AND lease_expires_at IS NULL AND error_code IS NULL
      AND analysis_markdown IS NOT NULL AND length(btrim(analysis_markdown)) > 0
      AND model_used IS NOT NULL
    )
  ),
  CONSTRAINT ck_analyses_failed_state CHECK (
    status <> 'failed'
    OR (completed_at IS NOT NULL AND lease_expires_at IS NULL AND error_code IS NOT NULL AND analysis_markdown IS NULL)
  ),
  CONSTRAINT ck_analyses_markdown_length CHECK (analysis_markdown IS NULL OR char_length(analysis_markdown) <= 100000),
  CONSTRAINT ck_analyses_report_is_object CHECK (deterministic_report IS NULL OR jsonb_typeof(deterministic_report) = 'object'),
  CONSTRAINT ck_analyses_latency_non_negative CHECK (latency_ms IS NULL OR latency_ms >= 0),
  CONSTRAINT ck_analyses_completed_after_created CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE INDEX ix_analyses_organization_id_created_at ON public.analyses (organization_id, created_at DESC);
CREATE INDEX ix_analyses_organization_id_campaign_id_created_at
  ON public.analyses (organization_id, campaign_id, created_at DESC);

-- One row per provider attempt. A 'reserved' row exists before the call, so an attempt is never invisible.
-- UNIQUE (analysis_id, attempt_kind): an analysis has at most one primary and one fallback attempt.
CREATE TABLE public.analysis_usage (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  analysis_id uuid NOT NULL,
  attempt_kind varchar(16) NOT NULL,
  model varchar(200) NOT NULL,
  status varchar(16) NOT NULL,
  error_code varchar(64),
  usage_reported boolean NOT NULL DEFAULT false,
  prompt_tokens integer,
  completion_tokens integer,
  cost_micros bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT pk_analysis_usage PRIMARY KEY (id),
  CONSTRAINT uq_analysis_usage_analysis_attempt UNIQUE (analysis_id, attempt_kind),
  CONSTRAINT fk_analysis_usage_analysis
    FOREIGN KEY (organization_id, analysis_id) REFERENCES public.analyses (organization_id, id) ON DELETE RESTRICT,
  CONSTRAINT ck_analysis_usage_attempt_kind_valid CHECK (attempt_kind IN ('primary', 'fallback')),
  CONSTRAINT ck_analysis_usage_status_valid CHECK (status IN ('reserved', 'succeeded', 'failed')),
  CONSTRAINT ck_analysis_usage_model_not_blank CHECK (length(btrim(model)) > 0),
  CONSTRAINT ck_analysis_usage_error_code_valid CHECK (
    error_code IS NULL OR error_code IN (
      'AI_TIMEOUT', 'AI_UNAVAILABLE', 'AI_INVALID_RESPONSE', 'AI_NOT_CONFIGURED', 'ANALYSIS_ABANDONED', 'INTERNAL_ERROR'
    )
  ),
  CONSTRAINT ck_analysis_usage_state CHECK (
    (status = 'reserved' AND error_code IS NULL AND completed_at IS NULL AND usage_reported = false)
    OR (status = 'succeeded' AND error_code IS NULL AND completed_at IS NOT NULL)
    OR (status = 'failed' AND error_code IS NOT NULL AND completed_at IS NOT NULL)
  ),
  CONSTRAINT ck_analysis_usage_reported_values CHECK (
    usage_reported OR (prompt_tokens IS NULL AND completion_tokens IS NULL AND cost_micros IS NULL)
  ),
  CONSTRAINT ck_analysis_usage_values_non_negative CHECK (
    (prompt_tokens IS NULL OR prompt_tokens >= 0)
    AND (completion_tokens IS NULL OR completion_tokens >= 0)
    AND (cost_micros IS NULL OR cost_micros >= 0)
  ),
  CONSTRAINT ck_analysis_usage_completed_after_created CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE INDEX ix_analysis_usage_organization_id_created_at
  ON public.analysis_usage (organization_id, created_at DESC);

-- Analyses: never deleted; once completed, nothing changes; while running, only the state and lease may change.
CREATE FUNCTION public.analyses_guard_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'analyses is append-only: DELETE is not allowed';
  END IF;
  IF OLD.status <> 'running' THEN
    RAISE EXCEPTION 'analyses is append-only: a completed analysis is immutable';
  END IF;
  IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
    OR NEW.campaign_id IS DISTINCT FROM OLD.campaign_id
    OR NEW.metric_entry_id IS DISTINCT FROM OLD.metric_entry_id
    OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
    OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
    OR NEW.input_hash IS DISTINCT FROM OLD.input_hash
    OR NEW.prompt_version IS DISTINCT FROM OLD.prompt_version
    OR NEW.model_requested IS DISTINCT FROM OLD.model_requested
    OR NEW.deterministic_report IS DISTINCT FROM OLD.deterministic_report
    OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'analyses is append-only: the inputs of an analysis are immutable';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_analyses_guard_change
  BEFORE UPDATE OR DELETE ON public.analyses
  FOR EACH ROW EXECUTE FUNCTION public.analyses_guard_change();

-- Attempts: a reserved attempt is completed once; a recorded attempt is never changed or deleted.
CREATE FUNCTION public.analysis_usage_guard_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'analysis_usage is append-only: DELETE is not allowed';
  END IF;
  IF OLD.status <> 'reserved' THEN
    RAISE EXCEPTION 'analysis_usage is append-only: a recorded attempt is immutable';
  END IF;
  IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
    OR NEW.analysis_id IS DISTINCT FROM OLD.analysis_id
    OR NEW.attempt_kind IS DISTINCT FROM OLD.attempt_kind
    OR NEW.model IS DISTINCT FROM OLD.model
    OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'analysis_usage is append-only: the identity of an attempt is immutable';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_analysis_usage_guard_change
  BEFORE UPDATE OR DELETE ON public.analysis_usage
  FOR EACH ROW EXECUTE FUNCTION public.analysis_usage_guard_change();

-- Closed to the managed APIs until identity is verified: RLS on, no policies (see supabase/README.md, section 8).
ALTER TABLE public.analyses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analysis_usage ENABLE ROW LEVEL SECURITY;

COMMIT;
