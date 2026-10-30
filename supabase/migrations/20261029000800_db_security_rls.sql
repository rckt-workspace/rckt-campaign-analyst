-- Phase 2.7B2: least-privilege roles, identity-scoped row level security and the operator functions.
--
-- Prerequisite: the roles created by scripts/db_roles/provision_roles.sql (roles are never created here: CREATE ROLE is
-- cluster-wide and must be reviewed by a person). This file only refers to them.
--
-- Ownership: the objects created by 0001-0008 are owned by the migrator. They move to rckt_schema_owner first, while the
-- migrator still owns them. Then this file runs as rckt_schema_owner (SET LOCAL ROLE): functions and policies created
-- below are owned by it. The migrator must be a member of rckt_schema_owner.
--
-- Identity: rckt_app reads only rows of the organizations where the verified subject (app.subject, set per transaction
-- with set_config(..., true) by the application) has a membership. A missing setting matches nothing (fail closed).
-- Operators never query tables directly: they call SECURITY DEFINER functions with a fixed search_path, validated
-- parameters and minimal results (see the operator section).
--
-- Not changed: data, the append-only triggers, and the analysis lifecycle rules. Existing tests keep running as the owner.

BEGIN;

-- --- Preconditions -------------------------------------------------------------------------------------------------------

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(r, ', ') INTO missing
    FROM unnest(ARRAY['rckt_schema_owner', 'rckt_migrator', 'rckt_app', 'rckt_operator']) AS r
    WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'roles missing: % (run scripts/db_roles/provision_roles.sql first)', missing;
    END IF;
    IF NOT pg_has_role('rckt_migrator', 'rckt_schema_owner', 'MEMBER') THEN
        RAISE EXCEPTION 'rckt_migrator must be a member of rckt_schema_owner';
    END IF;
END $$;

-- Identity sequences follow their table (PostgreSQL refuses to change them directly), so only tables are listed below.
-- --- Ownership: the schema first, then every object in it (run by the migrator, which owns them now) ----------------
-- In PostgreSQL 15 and later public belongs to the database owner. The schema must belong to rckt_schema_owner before
-- the objects are moved, because the new owner needs CREATE on the schema.
ALTER SCHEMA public OWNER TO rckt_schema_owner;


DO $$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT c.relname AS name
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
    LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO rckt_schema_owner', obj.name);
    END LOOP;
    FOR obj IN
        SELECT p.oid::regprocedure::text AS signature
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO rckt_schema_owner', obj.signature);
    END LOOP;
END $$;

SET LOCAL ROLE rckt_schema_owner;

-- Future functions. Verified on PostgreSQL 16: a GLOBAL default (no IN SCHEMA) makes a function that rckt_schema_owner
-- creates from now on executable by no one but its owner. The IN SCHEMA variant creates no rule and has no effect.
-- Explicit GRANT EXECUTE still works. The rule belongs to rckt_schema_owner: functions created by another role are not
-- affected (verified). The downgrade restores the built-in default.
ALTER DEFAULT PRIVILEGES FOR ROLE rckt_schema_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- --- Identity helpers (SECURITY INVOKER: RLS applies to their queries) ---------------------------------------------------

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

-- --- Privileges: nothing for PUBLIC, anon or authenticated; the application and the operator get no TRUNCATE, REFERENCES
-- or TRIGGER ----------------------------------------------------------------------------------------------------------

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;

DO $$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', api_role);
        END IF;
    END LOOP;
END $$;

REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM rckt_app, rckt_operator;

GRANT USAGE ON SCHEMA public TO rckt_app, rckt_operator;
GRANT SELECT ON public.organizations TO rckt_app;
GRANT SELECT ON public.organization_memberships TO rckt_app;
GRANT SELECT, INSERT ON public.app_users TO rckt_app;
GRANT UPDATE (email) ON public.app_users TO rckt_app;
GRANT SELECT, INSERT, UPDATE ON public.campaigns TO rckt_app;
GRANT SELECT, INSERT ON public.campaign_metric_entries TO rckt_app;
GRANT SELECT ON public.analysis_quotas TO rckt_app;
GRANT SELECT, INSERT, UPDATE ON public.analyses TO rckt_app;
GRANT SELECT, INSERT, UPDATE ON public.analysis_usage TO rckt_app;
GRANT SELECT, INSERT, UPDATE ON public.analysis_quota_usage TO rckt_app;
GRANT SELECT, INSERT ON public.audit_events TO rckt_app;
GRANT USAGE ON SEQUENCE public.audit_events_id_seq TO rckt_app;

-- --- Audit attribution of the database principal --------------------------------------------------------------------------
-- session_user is the login that connected. For operator events it is the person's login, so the operator is traceable
-- without writing a fake actor_user_id. Existing rows keep NULL: they were written before this attribution existed.

ALTER TABLE public.audit_events ADD COLUMN db_principal varchar(63);
ALTER TABLE public.audit_events ALTER COLUMN db_principal SET DEFAULT session_user;
ALTER TABLE public.audit_events ADD CONSTRAINT ck_audit_events_operator_principal
    CHECK (source <> 'operator_cli' OR db_principal IS NOT NULL) NOT VALID;

-- --- Row level security policies for rckt_app ---------------------------------------------------------------------------

-- app_users: the row of the verified subject only.
CREATE POLICY app_users_self_select ON public.app_users FOR SELECT TO rckt_app
    USING (external_subject = current_setting('app.subject', true));
CREATE POLICY app_users_self_insert ON public.app_users FOR INSERT TO rckt_app
    WITH CHECK (external_subject = current_setting('app.subject', true));
CREATE POLICY app_users_self_update ON public.app_users FOR UPDATE TO rckt_app
    USING (external_subject = current_setting('app.subject', true))
    WITH CHECK (external_subject = current_setting('app.subject', true));

-- organization_memberships: the caller's own rows only. Needed by GET /api/v1/me.
CREATE POLICY memberships_own_select ON public.organization_memberships FOR SELECT TO rckt_app
    USING (user_id = public.current_app_user_id());

-- organizations: visible when the caller has a membership there. Created only by the operator functions.
CREATE POLICY organizations_member_select ON public.organizations FOR SELECT TO rckt_app
    USING (public.app_role_of(id) IS NOT NULL);

-- analysis_quotas: every member reads the limit. The quota ledger joins this table on each claim.
CREATE POLICY analysis_quotas_member_select ON public.analysis_quotas FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);

-- campaigns: members read; owner and analyst create; owner edits.
CREATE POLICY campaigns_member_select ON public.campaigns FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);
CREATE POLICY campaigns_writer_insert ON public.campaigns FOR INSERT TO rckt_app
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));
CREATE POLICY campaigns_owner_update ON public.campaigns FOR UPDATE TO rckt_app
    USING (public.app_role_of(organization_id) = 'owner')
    WITH CHECK (public.app_role_of(organization_id) = 'owner');

-- campaign_metric_entries: members read; owner and analyst append. The append-only trigger refuses UPDATE and DELETE.
CREATE POLICY metric_entries_member_select ON public.campaign_metric_entries FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);
CREATE POLICY metric_entries_writer_insert ON public.campaign_metric_entries FOR INSERT TO rckt_app
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));

-- analyses: members read. Owner and analyst create as themselves, and move the lifecycle forward. The guard trigger
-- decides which transitions are legal.
CREATE POLICY analyses_member_select ON public.analyses FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);
CREATE POLICY analyses_writer_insert ON public.analyses FOR INSERT TO rckt_app
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst')
                AND requested_by = public.current_app_user_id());
CREATE POLICY analyses_writer_update ON public.analyses FOR UPDATE TO rckt_app
    USING (public.app_role_of(organization_id) IN ('owner', 'analyst'))
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));

-- analysis_usage: members read. Owner and analyst reserve and complete provider attempts. Only the status, error,
-- timing and usage columns change after insert, and the guard trigger refuses any other change.
CREATE POLICY analysis_usage_member_select ON public.analysis_usage FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);
CREATE POLICY analysis_usage_writer_insert ON public.analysis_usage FOR INSERT TO rckt_app
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));
CREATE POLICY analysis_usage_writer_update ON public.analysis_usage FOR UPDATE TO rckt_app
    USING (public.app_role_of(organization_id) IN ('owner', 'analyst'))
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));

-- Quota ledger: members read the month; owner and analyst take and give back units.
CREATE POLICY quota_usage_member_select ON public.analysis_quota_usage FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) IS NOT NULL);
CREATE POLICY quota_usage_writer_insert ON public.analysis_quota_usage FOR INSERT TO rckt_app
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));
CREATE POLICY quota_usage_writer_update ON public.analysis_quota_usage FOR UPDATE TO rckt_app
    USING (public.app_role_of(organization_id) IN ('owner', 'analyst'))
    WITH CHECK (public.app_role_of(organization_id) IN ('owner', 'analyst'));

-- audit_events: the owner reads. The application appends only the events it really produces, each one tied to what the
-- transaction did. There is no UPDATE or DELETE policy, and the append-only trigger refuses both anyway.
CREATE POLICY audit_owner_select ON public.audit_events FOR SELECT TO rckt_app
    USING (public.app_role_of(organization_id) = 'owner');

-- A user event is attributed to the verified caller, and its resource must be an analysis of that organization (for
-- analysis.created) or the usage report read by an owner (usage.report_accessed).
CREATE POLICY audit_user_insert ON public.audit_events FOR INSERT TO rckt_app
    WITH CHECK (
        source = 'user'
        AND actor_user_id = public.current_app_user_id()
        AND (
            (action = 'analysis.created'
                AND resource_type = 'analysis'
                AND public.app_role_of(organization_id) IN ('owner', 'analyst')
                AND EXISTS (SELECT 1 FROM public.analyses AS a
                            WHERE a.organization_id = audit_events.organization_id
                              AND a.id::text = audit_events.resource_id))
            OR
            (action = 'usage.report_accessed'
                AND resource_type = 'usage_report'
                AND public.app_role_of(organization_id) = 'owner')
        )
    );

-- An internal event is the outcome of an analysis that the same transaction finalizes or abandons: it has no actor, and
-- its resource must be an analysis of the organization written by an owner or analyst.
CREATE POLICY audit_internal_insert ON public.audit_events FOR INSERT TO rckt_app
    WITH CHECK (
        source = 'internal'
        AND actor_user_id IS NULL
        AND action IN ('analysis.succeeded', 'analysis.failed')
        AND resource_type = 'analysis'
        AND public.app_role_of(organization_id) IN ('owner', 'analyst')
        AND EXISTS (SELECT 1 FROM public.analyses AS a
                    WHERE a.organization_id = audit_events.organization_id
                      AND a.id::text = audit_events.resource_id)
    );

-- --- Operator functions: the only path of the CLI into the database --------------------------------------------------
-- Each function: SECURITY DEFINER, owned by rckt_schema_owner (it bypasses RLS because the owner is not forced), with a
-- fixed search_path, validated parameters, a single purpose, no dynamic SQL, and EXECUTE for rckt_operator only. Each
-- write and its audit event share one transaction: a refused audit rolls the change back.

CREATE FUNCTION public.operator_create_organization(p_name text, p_slug text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
DECLARE
    v_id uuid;
BEGIN
    IF p_name IS NULL OR btrim(p_name) = '' OR char_length(p_name) > 120 THEN
        RAISE EXCEPTION 'INVALID_NAME' USING ERRCODE = 'P0001';
    END IF;
    IF p_slug IS NULL OR p_slug !~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$' THEN
        RAISE EXCEPTION 'INVALID_SLUG' USING ERRCODE = 'P0001';
    END IF;
    INSERT INTO public.organizations (name, slug) VALUES (p_name, p_slug) RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

CREATE FUNCTION public.operator_provision_user(p_subject text, p_email text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
DECLARE
    v_id uuid;
BEGIN
    IF p_subject IS NULL OR btrim(p_subject) = '' OR char_length(p_subject) > 255 THEN
        RAISE EXCEPTION 'INVALID_SUBJECT' USING ERRCODE = 'P0001';
    END IF;
    IF p_email IS NOT NULL AND char_length(p_email) > 320 THEN
        RAISE EXCEPTION 'INVALID_EMAIL' USING ERRCODE = 'P0001';
    END IF;
    SELECT u.id INTO v_id FROM public.app_users AS u WHERE u.external_subject = p_subject FOR UPDATE;
    IF NOT FOUND THEN
        INSERT INTO public.app_users (external_subject, email) VALUES (p_subject, p_email) RETURNING id INTO v_id;
    ELSIF p_email IS NOT NULL AND p_email <> '' THEN
        UPDATE public.app_users AS u SET email = p_email WHERE u.id = v_id AND u.email IS DISTINCT FROM p_email;
    END IF;
    RETURN v_id;
END
$$;

CREATE FUNCTION public.operator_grant_membership(p_organization_slug text, p_subject text, p_email text, p_role text)
    RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org uuid;
    v_user uuid;
    v_matches integer;
    v_membership uuid;
BEGIN
    IF (p_subject IS NULL) = (p_email IS NULL) THEN
        RAISE EXCEPTION 'SUBJECT_OR_EMAIL_REQUIRED' USING ERRCODE = 'P0001';
    END IF;
    IF p_role NOT IN ('owner', 'analyst', 'client_viewer') THEN
        RAISE EXCEPTION 'INVALID_ROLE' USING ERRCODE = 'P0001';
    END IF;
    SELECT o.id INTO v_org FROM public.organizations AS o WHERE o.slug = p_organization_slug;
    IF v_org IS NULL THEN
        RAISE EXCEPTION 'ORGANIZATION_NOT_FOUND' USING ERRCODE = 'P0001';
    END IF;
    IF p_subject IS NOT NULL THEN
        SELECT u.id INTO v_user FROM public.app_users AS u WHERE u.external_subject = p_subject;
    ELSE
        SELECT count(*) INTO v_matches FROM public.app_users AS u WHERE u.email = lower(btrim(p_email));
        IF v_matches > 1 THEN
            RAISE EXCEPTION 'AMBIGUOUS_EMAIL' USING ERRCODE = 'P0001';
        END IF;
        SELECT u.id INTO v_user FROM public.app_users AS u WHERE u.email = lower(btrim(p_email));
    END IF;
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'USER_NOT_FOUND' USING ERRCODE = 'P0001';
    END IF;
    IF EXISTS (SELECT 1 FROM public.organization_memberships AS m
               WHERE m.organization_id = v_org AND m.user_id = v_user) THEN
        RAISE EXCEPTION 'MEMBERSHIP_EXISTS' USING ERRCODE = 'P0001';
    END IF;
    INSERT INTO public.organization_memberships (organization_id, user_id, role)
    VALUES (v_org, v_user, p_role)
    RETURNING id INTO v_membership;
    INSERT INTO public.audit_events (organization_id, source, action, resource_type, resource_id, metadata)
    VALUES (v_org, 'operator_cli', 'membership.granted', 'organization_membership', v_membership::text,
            jsonb_build_object('role', p_role));
    RETURN v_membership;
END
$$;

CREATE FUNCTION public.operator_set_analysis_quota(p_organization_slug text, p_plan text, p_monthly_limit integer)
    RETURNS TABLE (changed boolean, previous_plan text, previous_monthly_analysis_limit integer)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
DECLARE
    v_org uuid;
    v_previous_plan text;
    v_previous_limit integer;
BEGIN
    IF p_plan NOT IN ('pilot', 'standard', 'enterprise') THEN
        RAISE EXCEPTION 'INVALID_PLAN' USING ERRCODE = 'P0001';
    END IF;
    IF p_monthly_limit IS NULL OR p_monthly_limit < 0 THEN
        RAISE EXCEPTION 'INVALID_LIMIT' USING ERRCODE = 'P0001';
    END IF;
    SELECT o.id INTO v_org FROM public.organizations AS o WHERE o.slug = p_organization_slug;
    IF v_org IS NULL THEN
        RAISE EXCEPTION 'ORGANIZATION_NOT_FOUND' USING ERRCODE = 'P0001';
    END IF;
    SELECT q.plan, q.monthly_analysis_limit INTO v_previous_plan, v_previous_limit
    FROM public.analysis_quotas AS q WHERE q.organization_id = v_org FOR UPDATE;
    IF FOUND AND v_previous_plan = p_plan AND v_previous_limit = p_monthly_limit THEN
        RETURN QUERY SELECT false, v_previous_plan, v_previous_limit;
        RETURN;
    END IF;
    INSERT INTO public.analysis_quotas (organization_id, plan, monthly_analysis_limit)
    VALUES (v_org, p_plan, p_monthly_limit)
    ON CONFLICT (organization_id) DO UPDATE
        SET plan = EXCLUDED.plan,
            monthly_analysis_limit = EXCLUDED.monthly_analysis_limit,
            updated_at = now();
    INSERT INTO public.audit_events (organization_id, source, action, resource_type, resource_id, metadata)
    VALUES (v_org, 'operator_cli', 'quota.updated', 'analysis_quota', v_org::text,
            jsonb_build_object('plan', p_plan, 'monthly_analysis_limit', p_monthly_limit,
                               'previous_plan', v_previous_plan,
                               'previous_monthly_analysis_limit', v_previous_limit));
    RETURN QUERY SELECT true, v_previous_plan, v_previous_limit;
END
$$;

REVOKE ALL ON FUNCTION public.operator_create_organization(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.operator_provision_user(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.operator_grant_membership(text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.operator_set_analysis_quota(text, text, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.operator_create_organization(text, text) TO rckt_operator;
GRANT EXECUTE ON FUNCTION public.operator_provision_user(text, text) TO rckt_operator;
GRANT EXECUTE ON FUNCTION public.operator_grant_membership(text, text, text, text) TO rckt_operator;
GRANT EXECUTE ON FUNCTION public.operator_set_analysis_quota(text, text, integer) TO rckt_operator;

RESET ROLE;

COMMIT;
