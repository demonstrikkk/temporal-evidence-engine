-- The inherited substrate expects Supabase's role set.
--
-- Migrations 001, 002, 003 and 007 create row-level-security policies and
-- grants `TO anon, authenticated` - the roles every Supabase project has. On a
-- plain PostgreSQL those roles do not exist, and migration 001 aborts with
-- `role "anon" does not exist`, so the seeded database can never be created
-- outside Supabase even though the product only needs schema `tev`.
--
-- Creating them here makes the migration set portable. They are NOLOGIN roles
-- with no password and no memberships: a name a policy can reference, not an
-- account anyone can connect as. On Supabase the same roles already exist and
-- every branch below is a no-op, so this file stays idempotent there too.
--
-- This runs first because the policies that need the roles are in 001. It is
-- additive: it creates no tables, changes no upstream DDL, and grants nothing.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        -- The substrate's server-side role. Still NOLOGIN: the backend connects
        -- as its own database user and does not impersonate this one.
        CREATE ROLE service_role NOLOGIN;
    END IF;
END
$$;

COMMENT ON ROLE anon IS
    'Policy target for the inherited substrate; NOLOGIN, created for portability.';
COMMENT ON ROLE authenticated IS
    'Policy target for the inherited substrate; NOLOGIN, created for portability.';
COMMENT ON ROLE service_role IS
    'Policy target for the inherited substrate; NOLOGIN, created for portability.';
