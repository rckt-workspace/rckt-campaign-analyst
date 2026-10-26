-- RCKT Campaign Analyst: persisted AI analyses and one row per provider HTTP call.
--
-- Incremental migration. Apply after 20261025000200_campaigns.sql, once per database. Do not edit it after it runs.
-- Equivalent to Alembic revision 0004_analyses in backend/migrations (must stay structurally identical).
-- Portable: plain PostgreSQL 13 or newer. No extensions, no project identifiers, no IF NOT EXISTS.
--
-- Isolation: every row carries organization_id, and every foreign key includes it.
-- Idempotency: UNIQUE (organization_id, idempotency_key). The claim is INSERT ... ON CONFLICT DO NOTHING, committed
-- before any provider call, so concurrent requests with one key produce one analysis.
-- Lease: a running analysis is live only until lease_expires_at. Attempts can be reserved and completed only while
-- the lease is live; an analysis is abandoned only after it expires. An expired lease never allows a new call.
-- Attempts: one row per HTTP call, reserved before the call. Ordered by attempt_number, at most four per analysis.
-- History: completed analyses and completed attempts cannot be changed or deleted (triggers below).
-- Usage: tokens and cost are stored only when the provider reports them (usage_reported). Never estimated here.
-- Row level security is ON with no policies, and no grants to anon or authenticated (see docs/PHASE2_ANALYSES.md).

BEGIN;

-- A metric entry is referenced together with its campaign, so it can only belong to that campaign.
ALTER TABLE public.campaign_metric_entries
  ADD CONSTRAINT uq_campaign_metric_entries_organization_campaign_id
  UNIQUE (organization_id, campaign_id, id);

-- One row per analysis request. Created in 'running' with the deterministic report and the input hash.
CREATE TABLE public.analyses (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  campaign_id uuid,
  metric_entry_id uuid,
  requested_by uuid NOT NULL,
  idempotency_key varchar(128) NOT NULL,
  input_hash char(64) NOT NULL,
  prompt_version varchar(64) NOT NULL,
  model_requested varchar(200) NOT NULL,
  deterministic_report jsonb NOT NULL,
  status varchar(16) NOT NULL,
  lease_expires_at timestamptz,
  error_code varchar(64),
  analysis_markdown text,
  model_used varchar(200),
  used_fallback boolean NOT NULL DEFAULT false,
  format_valid boolean,
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
  CONSTRAINT ck_analyses_report_is_object CHECK (jsonb_typeof(deterministic_report) = 'object'),
  CONSTRAINT ck_analyses_metric_entry_needs_campaign CHECK (metric_entry_id IS NULL OR campaign_id IS NOT NULL),
  CONSTRAINT ck_analyses_error_code_valid CHECK (
    error_code IS NULL OR error_code IN (
      'AI_TIMEOUT', 'AI_NETWORK', 'AI_EMPTY', 'AI_HTTP_ERROR', 'AI_INVALID_RESPONSE', 'AI_NOT_CONFIGURED', 'ANALYSIS_ABANDONED', 'INTERNAL_ERROR'
    )
  ),
  CONSTRAINT ck_analyses_running_state CHECK (
    status <> 'running'
    OR (
      lease_expires_at IS NOT NULL AND completed_at IS NULL AND error_code IS NULL
      AND analysis_markdown IS NULL AND model_used IS NULL AND format_valid IS NULL
      AND used_fallback = false AND latency_ms IS NULL
    )
  ),
  CONSTRAINT ck_analyses_succeeded_state CHECK (
    status <> 'succeeded'
    OR (
      completed_at IS NOT NULL AND lease_expires_at IS NULL AND error_code IS NULL
      AND analysis_markdown IS NOT NULL AND length(btrim(analysis_markdown)) > 0
      AND model_used IS NOT NULL AND format_valid IS NOT NULL
    )
  ),
  CONSTRAINT ck_analyses_failed_state CHECK (
    status <> 'failed'
    OR (
      completed_at IS NOT NULL AND lease_expires_at IS NULL AND error_code IS NOT NULL
      AND analysis_markdown IS NULL AND model_used IS NULL AND format_valid IS NULL AND used_fallback = false
    )
  ),
  CONSTRAINT ck_analyses_markdown_length
    CHECK (analysis_markdown IS NULL OR char_length(analysis_markdown) <= 100000),
  CONSTRAINT ck_analyses_latency_non_negative CHECK (latency_ms IS NULL OR latency_ms >= 0),
  CONSTRAINT ck_analyses_completed_after_created CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE INDEX ix_analyses_organization_id_created_at ON public.analyses (organization_id, created_at DESC);
CREATE INDEX ix_analyses_organization_id_campaign_id_created_at
  ON public.analyses (organization_id, campaign_id, created_at DESC);

-- One row per HTTP call to the provider, reserved before the call and completed after it.
-- step: 'initial' (the first request) or 'format_retry' (the repeat after an answer that broke the format).
-- model_role: 'primary' or 'fallback'. UNIQUE (analysis_id, step, model_role): no accidental duplicate calls.
-- UNIQUE (analysis_id, attempt_number): the order of calls is kept and cannot have gaps (trigger below).
CREATE TABLE public.analysis_usage (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  analysis_id uuid NOT NULL,
  attempt_number smallint NOT NULL,
  step varchar(16) NOT NULL,
  model_role varchar(16) NOT NULL,
  model varchar(200) NOT NULL,
  status varchar(16) NOT NULL,
  error_code varchar(64),
  http_status integer,
  usage_reported boolean NOT NULL DEFAULT false,
  prompt_tokens integer,
  completion_tokens integer,
  cost_micros bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT pk_analysis_usage PRIMARY KEY (id),
  CONSTRAINT uq_analysis_usage_analysis_attempt_number UNIQUE (analysis_id, attempt_number),
  CONSTRAINT uq_analysis_usage_analysis_step_role UNIQUE (analysis_id, step, model_role),
  CONSTRAINT fk_analysis_usage_analysis
    FOREIGN KEY (organization_id, analysis_id) REFERENCES public.analyses (organization_id, id) ON DELETE RESTRICT,
  CONSTRAINT ck_analysis_usage_attempt_number_range CHECK (attempt_number BETWEEN 1 AND 4),
  CONSTRAINT ck_analysis_usage_step_valid CHECK (step IN ('initial', 'format_retry')),
  CONSTRAINT ck_analysis_usage_model_role_valid CHECK (model_role IN ('primary', 'fallback')),
  CONSTRAINT ck_analysis_usage_model_not_blank CHECK (length(btrim(model)) > 0),
  CONSTRAINT ck_analysis_usage_status_valid CHECK (status IN ('reserved', 'succeeded', 'failed', 'unknown')),
  CONSTRAINT ck_analysis_usage_error_code_valid CHECK (
    error_code IS NULL OR error_code IN (
      'AI_TIMEOUT', 'AI_NETWORK', 'AI_EMPTY', 'AI_HTTP_ERROR', 'AI_INVALID_RESPONSE', 'AI_NOT_CONFIGURED', 'ANALYSIS_ABANDONED', 'INTERNAL_ERROR'
    )
  ),
  CONSTRAINT ck_analysis_usage_http_status_range
    CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
  CONSTRAINT ck_analysis_usage_state CHECK (
    (status = 'reserved' AND error_code IS NULL AND http_status IS NULL AND completed_at IS NULL AND usage_reported = false)
    OR (status = 'succeeded' AND error_code IS NULL AND completed_at IS NOT NULL)
    OR (status = 'failed' AND error_code IS NOT NULL AND completed_at IS NOT NULL)
    OR (status = 'unknown' AND error_code IS NOT NULL AND completed_at IS NOT NULL AND usage_reported = false)
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

-- Reservation rules. A call is reserved only when:
-- - the analysis is running and its lease is live;
-- - no other attempt of the analysis is still reserved (one call in flight);
-- - the attempt is the next number, and the first attempt is the primary model of the initial request;
-- - a fallback follows a failed primary of the same step, and a format retry follows a successful initial answer.
CREATE FUNCTION public.analysis_usage_before_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.analyses a
    WHERE a.organization_id = NEW.organization_id
      AND a.id = NEW.analysis_id
      AND a.status = 'running'
      AND a.lease_expires_at > now()
    FOR SHARE
  ) THEN
    RAISE EXCEPTION 'analysis_usage: an attempt needs a running analysis with a live lease';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.analysis_usage u
    WHERE u.analysis_id = NEW.analysis_id AND u.status = 'reserved'
  ) THEN
    RAISE EXCEPTION 'analysis_usage: the previous attempt of this analysis is still reserved';
  END IF;
  IF NEW.attempt_number <> COALESCE(
    (SELECT max(u.attempt_number) FROM public.analysis_usage u WHERE u.analysis_id = NEW.analysis_id), 0
  ) + 1 THEN
    RAISE EXCEPTION 'analysis_usage: attempts must be numbered consecutively from 1';
  END IF;
  IF NEW.attempt_number = 1 AND NOT (NEW.step = 'initial' AND NEW.model_role = 'primary') THEN
    RAISE EXCEPTION 'analysis_usage: the first attempt must be the primary model of the initial request';
  END IF;
  IF NEW.step = 'initial' AND NEW.model_role = 'fallback' AND NOT EXISTS (
    SELECT 1 FROM public.analysis_usage u
    WHERE u.analysis_id = NEW.analysis_id AND u.step = 'initial' AND u.model_role = 'primary' AND u.status = 'failed'
  ) THEN
    RAISE EXCEPTION 'analysis_usage: the fallback model is reserved only after the primary model failed';
  END IF;
  IF NEW.step = 'format_retry' AND NOT EXISTS (
    SELECT 1 FROM public.analysis_usage u
    WHERE u.analysis_id = NEW.analysis_id AND u.step = 'initial' AND u.status = 'succeeded'
  ) THEN
    RAISE EXCEPTION 'analysis_usage: a format retry needs an answer to the initial request';
  END IF;
  IF NEW.step = 'format_retry' AND NEW.model_role = 'fallback' AND NOT EXISTS (
    SELECT 1 FROM public.analysis_usage u
    WHERE u.analysis_id = NEW.analysis_id AND u.step = 'format_retry' AND u.model_role = 'primary' AND u.status = 'failed'
  ) THEN
    RAISE EXCEPTION 'analysis_usage: the fallback format retry is reserved only after the primary retry failed';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_analysis_usage_before_insert
  BEFORE INSERT ON public.analysis_usage
  FOR EACH ROW EXECUTE FUNCTION public.analysis_usage_before_insert();

-- Completing an attempt: only once, only while the lease is live. The abandonment path marks reserved attempts
-- 'unknown' with ANALYSIS_ABANDONED, and only after the lease has expired. Recorded attempts never change.
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
    OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
    OR NEW.step IS DISTINCT FROM OLD.step
    OR NEW.model_role IS DISTINCT FROM OLD.model_role
    OR NEW.model IS DISTINCT FROM OLD.model
    OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'analysis_usage is append-only: the identity of an attempt is immutable';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.analyses a
    WHERE a.id = OLD.analysis_id
      AND a.organization_id = OLD.organization_id
      AND a.status = 'running'
  ) THEN
    RAISE EXCEPTION 'analysis_usage: the analysis is no longer running';
  END IF;
  IF NEW.error_code = 'ANALYSIS_ABANDONED' THEN
    IF NOT EXISTS (
      SELECT 1 FROM public.analyses a WHERE a.id = OLD.analysis_id AND a.lease_expires_at <= now()
    ) THEN
      RAISE EXCEPTION 'analysis_usage: an attempt is abandoned only after the lease expires';
    END IF;
  ELSIF NOT EXISTS (
    SELECT 1 FROM public.analyses a WHERE a.id = OLD.analysis_id AND a.lease_expires_at > now()
  ) THEN
    RAISE EXCEPTION 'analysis_usage: an attempt cannot be completed after the lease expires';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_analysis_usage_guard_change
  BEFORE UPDATE OR DELETE ON public.analysis_usage
  FOR EACH ROW EXECUTE FUNCTION public.analysis_usage_guard_change();

-- Analyses: never deleted. A completed analysis never changes. Inputs never change. A running analysis:
-- - renews its lease only while the lease is live;
-- - completes only while the lease is live, with no reserved attempt, and a final answer that matches a succeeded attempt;
-- - is abandoned only after the lease has expired.
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
  IF NEW.status = 'running' THEN
    IF OLD.lease_expires_at <= now() THEN
      RAISE EXCEPTION 'analyses: an expired lease cannot be renewed';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.error_code = 'ANALYSIS_ABANDONED' THEN
    IF OLD.lease_expires_at > now() THEN
      RAISE EXCEPTION 'analyses: an analysis is abandoned only after its lease expires';
    END IF;
  ELSIF OLD.lease_expires_at <= now() THEN
    RAISE EXCEPTION 'analyses: an analysis cannot be completed after its lease expires';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.analysis_usage u WHERE u.analysis_id = OLD.id AND u.status = 'reserved'
  ) THEN
    RAISE EXCEPTION 'analyses: an analysis cannot be finalized while an attempt is reserved';
  END IF;
  IF NEW.status = 'succeeded' AND NOT EXISTS (
    SELECT 1 FROM public.analysis_usage u
    WHERE u.analysis_id = OLD.id
      AND u.status = 'succeeded'
      AND u.model = NEW.model_used
      AND u.model_role = CASE WHEN NEW.used_fallback THEN 'fallback' ELSE 'primary' END
  ) THEN
    RAISE EXCEPTION 'analyses: the final answer must match a succeeded attempt with the same model and role';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_analyses_guard_change
  BEFORE UPDATE OR DELETE ON public.analyses
  FOR EACH ROW EXECUTE FUNCTION public.analyses_guard_change();

-- Closed to the managed APIs until identity is verified: RLS on, no policies, no grants.
ALTER TABLE public.analyses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analysis_usage ENABLE ROW LEVEL SECURITY;

COMMIT;
