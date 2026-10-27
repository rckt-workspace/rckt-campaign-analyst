-- RCKT Campaign Analyst: exact provider cost of each analysis call.
--
-- Incremental migration. Apply after 20261025000500_analysis_quotas.sql, once per database. Do not edit it after it runs.
-- Equivalent to Alembic revision 0007_provider_cost in backend/migrations (must stay structurally identical).
-- Portable: plain PostgreSQL 13 or newer. No extensions, no project identifiers, no IF NOT EXISTS.
--
-- provider_cost keeps the exact number OpenRouter reports in the usage object, with its unit not yet confirmed (credits
-- or USD). It is NULL when the provider reported nothing, and 0 only when it reported 0. cost_micros (micro-USD)
-- stays NULL until the unit is confirmed; see docs/PHASE2_PROVIDER_USAGE.md. Nothing here is a monetary limit.

BEGIN;

ALTER TABLE public.analysis_usage ADD COLUMN provider_cost numeric(18, 8);

ALTER TABLE public.analysis_usage
  ADD CONSTRAINT ck_analysis_usage_provider_cost_non_negative
  CHECK (provider_cost IS NULL OR provider_cost >= 0);

-- A cost exists only when the provider reported usage for that call.
ALTER TABLE public.analysis_usage
  ADD CONSTRAINT ck_analysis_usage_provider_cost_reported
  CHECK (provider_cost IS NULL OR usage_reported);

COMMIT;
