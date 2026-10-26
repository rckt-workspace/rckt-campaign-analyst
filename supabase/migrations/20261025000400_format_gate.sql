-- RCKT Campaign Analyst: a succeeded analysis must have a report that follows the required format.
--
-- Incremental migration. Apply after 20261025000300_analyses.sql, once per database. Do not edit it after it runs.
-- Equivalent to Alembic revision 0005_format_gate in backend/migrations (must stay structurally identical).
--
-- Decision (docs/PHASE2_ANALYSES.md, section 11): an answer that still breaks the required format after the allowed
-- retry is stored as 'failed' with AI_INVALID_RESPONSE. It is never stored as 'succeeded'. Migration 0004 allowed
-- format_valid = false on succeeded rows; this migration closes that gap. Only the constraint changes: no data is
-- rewritten, and rows that violate the new rule cannot exist, because the application never wrote them.

BEGIN;

ALTER TABLE public.analyses DROP CONSTRAINT ck_analyses_succeeded_state;

ALTER TABLE public.analyses ADD CONSTRAINT ck_analyses_succeeded_state CHECK (
  status <> 'succeeded'
  OR (
    completed_at IS NOT NULL AND lease_expires_at IS NULL AND error_code IS NULL
    AND analysis_markdown IS NOT NULL AND length(btrim(analysis_markdown)) > 0
    AND model_used IS NOT NULL AND format_valid IS TRUE
  )
);

COMMIT;
