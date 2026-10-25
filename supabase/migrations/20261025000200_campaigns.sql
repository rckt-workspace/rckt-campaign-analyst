-- RCKT Campaign Analyst: campaigns and their metric entries.
--
-- Incremental migration. Apply after 20261025000100_foundation.sql, once per database.
-- Equivalent to Alembic revision 0003_campaigns in backend/migrations.
-- Portable: plain PostgreSQL 13 or newer. No extensions, no project identifiers, no IF NOT EXISTS.
--
-- Tenancy: every row carries organization_id. A metric entry cannot reference a campaign of another
-- organization: a composite foreign key enforces (organization_id, campaign_id) -> campaigns(organization_id, id).
--
-- Money: monetary amounts are numeric, never floating point. Investment and revenue use two decimals;
-- unit costs (CPC, CPL) use four, because they are often fractions of a currency unit.

BEGIN;

-- Campaigns of an organization. Archived campaigns keep their history; they are never deleted while entries exist.
CREATE TABLE public.campaigns (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  name varchar(2000) NOT NULL,
  channel varchar(2000),
  objective varchar(2000),
  period_label varchar(2000),
  currency char(3) NOT NULL DEFAULT 'COP',
  period_start date,
  period_end date,
  created_by uuid,
  archived_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_campaigns PRIMARY KEY (id),
  -- Target of the composite foreign key from campaign_metric_entries. It also guarantees (organization_id, id) is unique.
  CONSTRAINT uq_campaigns_organization_id_id UNIQUE (organization_id, id),
  CONSTRAINT fk_campaigns_organization_id
    FOREIGN KEY (organization_id) REFERENCES public.organizations (id) ON DELETE RESTRICT,
  CONSTRAINT fk_campaigns_created_by
    FOREIGN KEY (created_by) REFERENCES public.app_users (id) ON DELETE RESTRICT,
  CONSTRAINT ck_campaigns_name_not_blank CHECK (length(btrim(name)) > 0),
  CONSTRAINT ck_campaigns_currency_format CHECK (currency ~ '^[A-Z]{3}$'),
  CONSTRAINT ck_campaigns_period_order
    CHECK (period_start IS NULL OR period_end IS NULL OR period_end >= period_start)
);

CREATE INDEX ix_campaigns_organization_id_created_at ON public.campaigns (organization_id, created_at DESC);

-- Historical metric records of a campaign. Append-only: a correction is a new entry, never an update.
CREATE TABLE public.campaign_metric_entries (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  campaign_id uuid NOT NULL,
  source varchar(16) NOT NULL,
  raw_input text NOT NULL,
  period_label varchar(2000),
  period_start date,
  period_end date,
  investment numeric(14,2),
  impressions bigint,
  reach bigint,
  frequency numeric(12,4),
  clicks bigint,
  ctr numeric(9,4),
  cpc numeric(14,4),
  leads bigint,
  cpl numeric(14,4),
  conversions bigint,
  revenue numeric(14,2),
  roas numeric(12,4),
  previous_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_campaign_metric_entries PRIMARY KEY (id),
  -- The campaign must belong to the same organization as the entry.
  CONSTRAINT fk_campaign_metric_entries_campaign_scope
    FOREIGN KEY (organization_id, campaign_id) REFERENCES public.campaigns (organization_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_campaign_metric_entries_created_by
    FOREIGN KEY (created_by) REFERENCES public.app_users (id) ON DELETE RESTRICT,
  CONSTRAINT ck_campaign_metric_entries_source_valid CHECK (source IN ('form', 'text')),
  CONSTRAINT ck_campaign_metric_entries_raw_input_length CHECK (char_length(raw_input) <= 12000),
  CONSTRAINT ck_campaign_metric_entries_period_order
    CHECK (period_start IS NULL OR period_end IS NULL OR period_end >= period_start),
  CONSTRAINT ck_campaign_metric_entries_previous_is_object CHECK (jsonb_typeof(previous_metrics) = 'object'),
  -- Values are recorded as entered. Inconsistencies between metrics are warnings for the analyzer, not errors here.
  CONSTRAINT ck_campaign_metric_entries_non_negative CHECK (
    (investment IS NULL OR investment >= 0)
    AND (impressions IS NULL OR impressions >= 0)
    AND (reach IS NULL OR reach >= 0)
    AND (frequency IS NULL OR frequency >= 0)
    AND (clicks IS NULL OR clicks >= 0)
    AND (ctr IS NULL OR ctr >= 0)
    AND (cpc IS NULL OR cpc >= 0)
    AND (leads IS NULL OR leads >= 0)
    AND (cpl IS NULL OR cpl >= 0)
    AND (conversions IS NULL OR conversions >= 0)
    AND (revenue IS NULL OR revenue >= 0)
    AND (roas IS NULL OR roas >= 0)
  )
);

CREATE INDEX ix_campaign_metric_entries_campaign_created
  ON public.campaign_metric_entries (organization_id, campaign_id, created_at DESC);

-- Enforces append-only history for UPDATE and DELETE on every row. TRUNCATE is deliberately not blocked here:
-- it is an owner-only operation used by administration and test cleanup, and it is not reachable through APIs.
CREATE FUNCTION public.campaign_metric_entries_reject_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'campaign_metric_entries is append-only: % is not allowed', TG_OP;
END;
$$;

CREATE TRIGGER trg_campaign_metric_entries_append_only
  BEFORE UPDATE OR DELETE ON public.campaign_metric_entries
  FOR EACH ROW EXECUTE FUNCTION public.campaign_metric_entries_reject_change();

-- Row level security is ON with no policies, as in the foundation migration. Policies are written only after
-- identity integration is verified (see supabase/README.md).
ALTER TABLE public.campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_metric_entries ENABLE ROW LEVEL SECURITY;

COMMIT;
