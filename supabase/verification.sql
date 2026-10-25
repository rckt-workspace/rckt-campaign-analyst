-- Read-only verification of the foundation schema. Safe to run in any SQL editor; it changes nothing.
-- Expected results are described above each query. A mismatch means the database differs from
-- supabase/migrations/20261025000100_foundation.sql and must be investigated before use.

-- 1. The three tables exist in the public schema (expected: 3 rows).
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('organizations', 'app_users', 'organization_memberships')
ORDER BY table_name;

-- 2. Constraint names match the models (expected: 11 rows, names exactly as listed).
SELECT conrelid::regclass AS table_name, conname AS constraint_name, contype AS kind
FROM pg_constraint
WHERE connamespace = 'public'::regnamespace
  AND conrelid::regclass::text IN ('organizations', 'app_users', 'organization_memberships')
ORDER BY 1, 2;

-- 3. Row level security is enabled on all three tables with no policies (expected: 3 rows, rls_enabled = true,
--    policy_count = 0). Policies are added later, after identity integration is verified.
SELECT c.relname AS table_name,
       c.relrowsecurity AS rls_enabled,
       c.relforcerowsecurity AS rls_forced,
       (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname) AS policy_count
FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace
  AND c.relname IN ('organizations', 'app_users', 'organization_memberships')
ORDER BY 1;

-- 4. Indexes (expected: the primary keys, the unique constraints and ix_organization_memberships_user_id).
SELECT tablename, indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('organizations', 'app_users', 'organization_memberships')
ORDER BY 1, 2;

-- 5. Column types and defaults (expected: id uuid with gen_random_uuid(); created_at/updated_at timestamptz).
SELECT table_name, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('organizations', 'app_users', 'organization_memberships')
ORDER BY table_name, ordinal_position;

-- 6. Foreign key delete rules (expected: both foreign keys with delete_rule = RESTRICT).
SELECT conname AS constraint_name,
       confdeltype AS delete_action
FROM pg_constraint
WHERE contype = 'f'
  AND connamespace = 'public'::regnamespace
ORDER BY 1;

-- 7. The server supports gen_random_uuid() without extensions (expected: version_num >= 130000, true).
SELECT current_setting('server_version_num')::int >= 130000 AS supports_builtin_uuid,
       current_setting('server_version_num') AS version_num;

-- ===== Campaigns (migration 20261025000200_campaigns.sql) =====

-- 8. Both campaign tables exist (expected: 2 rows).
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('campaigns', 'campaign_metric_entries')
ORDER BY table_name;

-- 9. Append-only trigger is installed on campaign_metric_entries (expected: 1 row, tgenabled = 'O').
SELECT tgname AS trigger_name, tgenabled AS enabled
FROM pg_trigger
WHERE tgrelid = 'public.campaign_metric_entries'::regclass
  AND NOT tgisinternal
ORDER BY 1;

-- 10. Campaign constraint names (expected: 14 rows: 6 on campaigns and 8 on campaign_metric_entries, covering
--     primary, unique, foreign and check constraints). Names must match backend/app/db/models.py.
SELECT conrelid::regclass AS table_name, conname AS constraint_name, contype AS kind
FROM pg_constraint
WHERE connamespace = 'public'::regnamespace
  AND conrelid::regclass::text IN ('campaigns', 'campaign_metric_entries')
ORDER BY 1, 2;

-- 11. Row level security on campaign tables, no policies (expected: 2 rows, rls_enabled = true, policy_count = 0).
SELECT c.relname AS table_name,
       c.relrowsecurity AS rls_enabled,
       (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname) AS policy_count
FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace
  AND c.relname IN ('campaigns', 'campaign_metric_entries')
ORDER BY 1;

-- 12. Monetary and metric column types (expected: numeric with the scales documented in supabase/README.md section 11).
SELECT table_name, column_name, data_type, numeric_precision, numeric_scale
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('campaigns', 'campaign_metric_entries')
  AND (data_type = 'numeric' OR column_name IN ('impressions', 'reach', 'clicks', 'leads', 'conversions'))
ORDER BY table_name, column_name;
