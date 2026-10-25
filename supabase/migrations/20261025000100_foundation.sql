-- RCKT Campaign Analyst: foundation schema (organizations, app users, organization memberships).
--
-- Equivalent to Alembic revisions 0001_foundation + 0002_constraint_names_and_rls in backend/migrations.
-- Portable: plain PostgreSQL 13 or newer. It runs unchanged in the local development database,
-- the Lovable Cloud SQL editor, and a licensed Supabase project. It contains no project identifiers,
-- keys or passwords, and it does not use IF NOT EXISTS: an unexpected existing object must fail loudly.
--
-- Run once per database, in an empty schema state for these three tables. See supabase/README.md.

BEGIN;

-- gen_random_uuid() is built in from PostgreSQL 13. Stop early on older servers instead of failing later.
DO $$
BEGIN
  IF current_setting('server_version_num')::int < 130000 THEN
    RAISE EXCEPTION 'PostgreSQL 13 or newer is required (gen_random_uuid is built in from 13)';
  END IF;
END
$$;

-- Clients of RCKT. RCKT's own staff belong to an internal organization.
CREATE TABLE public.organizations (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  name varchar(120) NOT NULL,
  slug varchar(63) NOT NULL,
  status varchar(16) NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_organizations PRIMARY KEY (id),
  CONSTRAINT uq_organizations_slug UNIQUE (slug),
  CONSTRAINT ck_organizations_slug_format CHECK (slug ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'),
  CONSTRAINT ck_organizations_status_valid CHECK (status IN ('active', 'suspended'))
);

-- Local identity record for a person authenticated by the identity provider. No passwords are stored.
-- external_subject is the provider's stable user identifier (the token "sub" claim).
CREATE TABLE public.app_users (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  external_subject varchar(255) NOT NULL,
  email varchar(320),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_app_users PRIMARY KEY (id),
  CONSTRAINT uq_app_users_external_subject UNIQUE (external_subject)
);

-- A user has at most one role in an organization. Access decisions read this table.
CREATE TABLE public.organization_memberships (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL,
  user_id uuid NOT NULL,
  role varchar(32) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_organization_memberships PRIMARY KEY (id),
  CONSTRAINT fk_organization_memberships_organization_id
    FOREIGN KEY (organization_id) REFERENCES public.organizations (id) ON DELETE RESTRICT,
  CONSTRAINT fk_organization_memberships_user_id
    FOREIGN KEY (user_id) REFERENCES public.app_users (id) ON DELETE RESTRICT,
  CONSTRAINT uq_organization_memberships_org_user UNIQUE (organization_id, user_id),
  CONSTRAINT ck_organization_memberships_role_valid
    CHECK (role IN ('owner', 'analyst', 'client_viewer'))
);

CREATE INDEX ix_organization_memberships_user_id ON public.organization_memberships (user_id);

-- Row level security is ON with no policies yet. Roles exposed by managed APIs (for example anon and
-- authenticated) see no rows until policies are written and verified. The table owner, which FastAPI uses,
-- bypasses RLS because FORCE is not set. Do not add permissive policies here; see supabase/README.md.
ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_memberships ENABLE ROW LEVEL SECURITY;

COMMIT;
