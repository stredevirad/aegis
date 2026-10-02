DROP FUNCTION IF EXISTS aegis.admin_set_user_disabled(text, boolean);
DROP FUNCTION IF EXISTS aegis.org_id_for_slug(text);
REVOKE DELETE ON aegis.sessions FROM aegis_definer;
