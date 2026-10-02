-- Migration 0004: operator-only SECURITY DEFINER functions used by the `aegis admin` CLI.
-- Executable by aegis_owner (the operator's role) only; never by aegis_app.

-- Organizations are invisible without an organization context (FORCE RLS), even to the owner.
CREATE FUNCTION aegis.org_id_for_slug(p_slug text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$ SELECT o.id FROM aegis.organizations o WHERE o.slug = p_slug $$;

-- Disables (or re-enables) every account with this email and, when disabling, deletes its
-- sessions in every organization (ADR-011 amendment). Returns (user, organization) pairs so
-- the caller can revoke API keys per organization.
CREATE FUNCTION aegis.admin_set_user_disabled(p_email text, p_disabled boolean)
RETURNS TABLE (user_id uuid, org_id uuid)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
BEGIN
    UPDATE aegis.users u SET disabled = p_disabled WHERE lower(u.email) = lower(p_email);
    IF p_disabled THEN
        DELETE FROM aegis.sessions s
         WHERE s.user_id IN (SELECT u.id FROM aegis.users u WHERE lower(u.email) = lower(p_email));
    END IF;
    RETURN QUERY
    SELECT m.user_id, m.org_id FROM aegis.memberships m
      JOIN aegis.users u ON u.id = m.user_id
     WHERE lower(u.email) = lower(p_email);
END $$;

GRANT DELETE ON aegis.sessions TO aegis_definer;
GRANT CREATE ON SCHEMA aegis TO aegis_definer;
DO $$
DECLARE f text;
BEGIN
    FOREACH f IN ARRAY ARRAY['aegis.org_id_for_slug(text)', 'aegis.admin_set_user_disabled(text, boolean)']
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO aegis_definer', f);
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO aegis_owner', f);
    END LOOP;
END $$;
REVOKE CREATE ON SCHEMA aegis FROM aegis_definer;
