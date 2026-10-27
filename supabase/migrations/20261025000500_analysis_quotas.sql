-- RCKT Campaign Analyst: analysis quotas per organization and UTC month.
--
-- Incremental migration. Apply after 20261025000400_format_gate.sql, once per database. Do not edit it after it runs.
-- Equivalent to Alembic revision 0006_analysis_quotas in backend/migrations (must stay structurally identical).
-- Portable: plain PostgreSQL 13 or newer. No extensions, no project identifiers, no IF NOT EXISTS.
--
-- Counting: one unit per analysis, reserved in the same transaction as its claim. Released only when no provider
-- call was ever reserved for that analysis (docs/PHASE2_QUOTAS.md, section 2). Enforcement is an application
-- setting; this migration only stores counts and limits. Nothing here is a monetary limit.
-- Isolation: every row carries organization_id; every foreign key includes it.
-- Row level security is ON with no policies and no grants to anon or authenticated, as in the previous migrations.

BEGIN;

-- The quota of an organization. Without a row, no limit is configured.
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
);

-- Units of one organization in one UTC month. period_start is always the first day of the month.
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
);

-- The month an analysis was counted in. NULL for analyses created before quotas: they are never counted.
ALTER TABLE public.analyses ADD COLUMN quota_period_start date;

ALTER TABLE public.analyses
  ADD CONSTRAINT ck_analyses_quota_period_first_day
  CHECK (quota_period_start IS NULL OR extract(day from quota_period_start) = 1);

ALTER TABLE public.analyses
  ADD CONSTRAINT fk_analyses_quota_usage
  FOREIGN KEY (organization_id, quota_period_start)
  REFERENCES public.analysis_quota_usage (organization_id, period_start) ON DELETE RESTRICT;

-- Closed to the managed APIs until identity is verified: RLS on, no policies, no grants.
ALTER TABLE public.analysis_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analysis_quota_usage ENABLE ROW LEVEL SECURITY;

COMMIT;
