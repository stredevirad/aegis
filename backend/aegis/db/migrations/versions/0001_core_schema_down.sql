-- Downgrade of migration 0001 (used by the upgrade/downgrade test on a fresh database).
DROP FUNCTION IF EXISTS aegis.reap_dead_jobs();
DROP FUNCTION IF EXISTS aegis.claim_job(text, integer, integer);
DROP FUNCTION IF EXISTS aegis.user_memberships(uuid);
DROP FUNCTION IF EXISTS aegis.user_upsert(text, text, text, boolean, text);
DROP FUNCTION IF EXISTS aegis.session_lookup(bytea);
DROP TABLE IF EXISTS aegis.audit_events;
DROP FUNCTION IF EXISTS aegis.audit_verify();
DROP FUNCTION IF EXISTS aegis.audit_chain();
DROP FUNCTION IF EXISTS aegis.audit_hash(uuid, bigint, timestamptz, text, text, text, text, text, jsonb, text, bytea);
DROP TABLE IF EXISTS aegis.root_causes, aegis.candidates, aegis.evidence_relations, aegis.evidence,
    aegis.snapshots, aegis.investigation_events, aegis.investigation_jobs, aegis.investigations,
    aegis.incidents, aegis.sessions, aegis.memberships, aegis.users, aegis.organizations;
DROP FUNCTION IF EXISTS aegis.current_user_id();
DROP FUNCTION IF EXISTS aegis.current_org();
