-- Migration 0008: reconcile remediations stranded mid-execution.
-- A crash (or cancelled request) between committing 'executing'/'rolling_back' and the final
-- update would leave the row in that state forever — and, because both states are "open",
-- block every new proposal for the same action and incident. The worker's maintenance loop
-- claims such rows (leased, skip-locked, cross-organization) and marks them failed after
-- re-reading the target; it never re-executes.
CREATE FUNCTION aegis.claim_stuck_remediation(p_worker text, p_stale_seconds integer, p_lease_seconds integer)
RETURNS TABLE (proposal_id uuid, org_id uuid, status text)
    LANGUAGE sql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    UPDATE aegis.remediation_proposals p
       SET verify_lease_owner = p_worker,
           verify_lease_until = now() + make_interval(secs => p_lease_seconds)
     WHERE p.id = (
        SELECT s.id FROM aegis.remediation_proposals s
         WHERE s.status IN ('executing', 'rolling_back')
           AND s.updated_at < now() - make_interval(secs => p_stale_seconds)
           AND (s.verify_lease_until IS NULL OR s.verify_lease_until < now())
         ORDER BY s.updated_at
         LIMIT 1
           FOR UPDATE SKIP LOCKED)
    RETURNING p.id, p.org_id, p.status
$$;
GRANT CREATE ON SCHEMA aegis TO aegis_definer;
ALTER FUNCTION aegis.claim_stuck_remediation(text, integer, integer) OWNER TO aegis_definer;
REVOKE ALL ON FUNCTION aegis.claim_stuck_remediation(text, integer, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION aegis.claim_stuck_remediation(text, integer, integer) TO aegis_app;
REVOKE CREATE ON SCHEMA aegis FROM aegis_definer;
