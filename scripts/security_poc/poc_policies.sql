-- Proposed least-privilege model for the FastAPI runtime role (rckt_app). Phase 2.7B1, prototype.
--
-- NOT A MIGRATION. This file runs only against a disposable PostgreSQL instance created for the proof of concept
-- (scripts/security_poc/run_poc.py). It must not be applied to rckt, to Lovable Cloud or to Supabase.
--
-- Run as the owner role (rckt_owner). The owner is not a superuser and has no BYPASSRLS; it owns the tables, so it
-- bypasses RLS only because FORCE ROW LEVEL SECURITY is not set (the migrations keep it unset on purpose).
--
-- Identity contract: the FastAPI process sets app.subject inside each transaction, with set_config(..., true), after
-- verifying the bearer token. Nothing else sets it. A missing or empty value matches no row (fail closed).
-- Every function below is SECURITY INVOKER: it never runs with the owner's rights, so RLS applies to its queries.

-- ---------------------------------------------------------------------------------------------------------------
-- Helpers (SECURITY INVOKER, STABLE, fixed search_path). No recursion: memberships' policy depends only on app_users,
-- and app_users' policy depends only on the session setting.
-- ---------------------------------------------------------------------------------------------------------------

CREATE FUNCTION public.current_app_user_id() RETURNS uuid
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, public
AS $$
    SELECT u.id FROM public.app_users AS u
    WHERE u.external_subject = current_setting('app.subject', true)
$$;

CREATE FUNCTION public.app_role_of(p_organization_id uuid) RETURNS text
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, public
AS $$
    SELECT m.role FROM public.organization_memberships AS m
    WHERE m.organization_id = p_organization_id AND m.user_id = public.current_app_user_id()
$$;

REVOKE ALL ON FUNCTION public.current_app_user_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.app_role_of(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_app_user_id() TO rckt_app;
GRANT EXECUTE ON FUNCTION public.app_role_of(uuid) TO rckt_app;

-- ---------------------------------------------------------------------------------------------------------------
-- Grants: only what the current code paths need. No TRUNCATE, REFERENCES or TRIGGER. No grants to anon,
-- authenticated or PUBLIC.
-- ---------------------------------------------------------------------------------------------------------------

GRANT USAGE ON SCHEMA public TO rckt_app;
GRANT SELECT, INSERT ON public.app_users TO rckt_app;
GRANT SELECT ON public.organizations TO rckt_app;
GRANT SELECT ON public.organization_memberships TO rckt_app;
GRANT SELECT, INSERT, UPDATE ON public.campaigns TO rckt_app;
GRANT SELECT, INSERT ON public.campaign_metric_entries TO rckt_app;
GRANT SELECT, INSERT, UPDATE ON public.analyses TO rckt_app;
GRANT SELECT, INSERT ON public.analysis_usage TO rckt_app;
GRANT SELECT ON public.analysis_quotas TO rckt_app;
GRANT SELECT, INSERT, UPDATE ON public.analysis_quota_usage TO rckt_app;
GRANT SELECT, INSERT ON public.audit_events TO rckt_app;
GRANT USAGE ON SEQUENCE public.audit_events_id_seq TO rckt_app;

-- ---------------------------------------------------------------------------------------------------------------
-- Policies. Every policy is restricted to rckt_app. Operator access is not in this file (see the design document).
-- ---------------------------------------------------------------------------------------------------------------

-- app_users: a person reads and creates only the row of the verified subject.
CREATE POLICY app_users_self_select ON public.app_users FOR SELECT TO rckt_app
    USING (external_subject = current_setting('app.subject', true));
CREATE POLICY app_users_self_insert ON public.app_users FOR INSERT TO rckt_app
    WITH CHECK (external_subject = current_setting('app.subject', true));

-- organization_memberships: only the caller's own memberships, which is what GET /api/v1/me needs.
CREATE POLICY memberships_own_select ON public.organization_memberships FOR SELECT TO rckt_app
    USING (user_id = public.current_app_user_id());

-- organizations: visible when the caller has a membership there.
CREATE POLICY organizations_member_select ON public.organizations FOR SELECT TO rckt_app
    USING (public.app_role_of(id) IS NOT NULL);

-- analysis_quotas: every member reads the limit. The conditional UPDATE of the quota ledger joins this table, so a
-- member who cannot read the limit would see zero rows and be refused as if the quota were exhausted.
CREATE POLICY analysis_quotas_member_select ON public.analysis_quotas FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);

-- campaigns and metric entries: members read; owner and analyst write; owner edits campaigns.
CREATE POLICY campaigns_member_select ON public.campaigns FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);
CREATE POLICY campaigns_writer_insert ON public.campaigns FOR INSERT TO rckt_app
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));
CREATE POLICY campaigns_owner_update ON public.campaigns FOR UPDATE TO rckt_app
    USING (public.app_role_of(organization_id) = 'owner')
    WITH CHECK (public.app_role_of(organization_id) = 'owner');

CREATE POLICY metric_entries_member_select ON public.campaign_metric_entries FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);
CREATE POLICY metric_entries_writer_insert ON public.campaign_metric_entries FOR INSERT TO rckt_app
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));

-- analyses: members read; owner and analyst create, as the caller. Updates pass through the guard trigger.
CREATE POLICY analyses_member_select ON public.analyses FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);
CREATE POLICY analyses_writer_insert ON public.analyses FOR INSERT TO rckt_app
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst')
                AND requested_by = public.current_app_user_id());
CREATE POLICY analyses_writer_update ON public.analyses FOR UPDATE TO rckt_app
    USING (public.app_role_of(organization_id) IN ('owner', 'analyst'))
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));

CREATE POLICY analysis_usage_member_select ON public.analysis_usage FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);
CREATE POLICY analysis_usage_writer_insert ON public.analysis_usage FOR INSERT TO rckt_app
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));

-- Quota ledger: members read the month; owner and analyst consume and release units.
CREATE POLICY quota_usage_member_select ON public.analysis_quota_usage FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);
CREATE POLICY quota_usage_writer_insert ON public.analysis_quota_usage FOR INSERT TO rckt_app
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));
CREATE POLICY quota_usage_writer_update ON public.analysis_quota_usage FOR UPDATE TO rckt_app
    USING (public.app_role_of(organization_id) IN ('owner', 'analyst'))
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));

-- audit_events: owner reads; the application inserts only user events, attributed to the caller, in an organization
-- the caller belongs to. No policy for UPDATE or DELETE, and the append-only trigger refuses them anyway.
CREATE POLICY audit_owner_select ON public.audit_events FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) = 'owner');
CREATE POLICY audit_user_insert ON public.audit_events FOR INSERT TO rckt_app
    WITH CHECK (source = 'user'
                AND actor_user_id = public.current_app_user_id()
                AND public.app_role_of(organization_id) IS NOT NULL);
