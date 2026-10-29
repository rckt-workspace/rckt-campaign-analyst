-- Provisioning of the RCKT database roles. NOT a migration: it runs once per environment, by a human operator,
-- with a role that may create roles (CREATE ROLE is cluster-wide). Never run it on a shared cluster without review.
--
-- Creates, when missing:
--   rckt_schema_owner  NOLOGIN  owns every object of schema public. No application session uses it.
--   rckt_migrator      LOGIN    member of rckt_schema_owner. Runs migrations only.
--   rckt_app           LOGIN    the FastAPI runtime. Owns nothing.
--   rckt_operator      NOLOGIN  group role that holds the operator's privileges (EXECUTE on the operator functions).
--                               Persons log in through their own LOGIN roles that are members of rckt_operator
--                               (see the comment at the end). This keeps the database principal in audit_events
--                               traceable to a person without falsifying actor_user_id.
--
-- It never alters an existing role's attributes. It refuses to continue if an existing RCKT role is a superuser or has
-- BYPASSRLS. It sets no passwords: credentials are set outside the repository, by the operator, after this script.
--
-- Usage (psql, as a role with CREATEROLE):  psql -v ON_ERROR_STOP=1 -f scripts/db_roles/provision_roles.sql

DO $$
DECLARE
    bad text;
BEGIN
    SELECT string_agg(rolname, ', ') INTO bad
    FROM pg_roles
    WHERE rolname IN ('rckt_schema_owner', 'rckt_migrator', 'rckt_app', 'rckt_operator')
      AND (rolsuper OR rolbypassrls);
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'refusing to continue: existing RCKT role(s) with SUPERUSER or BYPASSRLS: %', bad;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rckt_schema_owner') THEN
        CREATE ROLE rckt_schema_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rckt_migrator') THEN
        CREATE ROLE rckt_migrator LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rckt_app') THEN
        CREATE ROLE rckt_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rckt_operator') THEN
        CREATE ROLE rckt_operator NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE INHERIT;
    END IF;
END $$;

-- The migrator acts as the schema owner through membership (SET ROLE rckt_schema_owner), never as a table owner itself.
DO $$
BEGIN
    IF NOT pg_has_role('rckt_migrator', 'rckt_schema_owner', 'MEMBER') THEN
        EXECUTE 'GRANT rckt_schema_owner TO rckt_migrator';
    END IF;
END $$;

-- Guard: the application and operator roles must never be members of the owner role.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname IN ('rckt_app', 'rckt_operator') AND pg_has_role(r.rolname, 'rckt_schema_owner', 'MEMBER')) THEN
        RAISE EXCEPTION 'refusing to continue: rckt_app or rckt_operator is a member of rckt_schema_owner';
    END IF;
END $$;

-- Person-level operator logins (run by the operator for each person, outside this repository, with a password set
-- out of band). Example of the shape only; this script does not create them:
--   CREATE ROLE rckt_operator_<person> LOGIN NOSUPERUSER NOBYPASSRLS INHERIT;
--   GRANT rckt_operator TO rckt_operator_<person>;
