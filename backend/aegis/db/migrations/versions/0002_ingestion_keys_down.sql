-- Downgrade of migration 0002.
DROP FUNCTION IF EXISTS aegis.invitations_for_email(text);
DROP FUNCTION IF EXISTS aegis.api_key_lookup(bytea);
DROP FUNCTION IF EXISTS aegis.promote_deferred_jobs(integer);

CREATE OR REPLACE FUNCTION aegis.reap_dead_jobs()
RETURNS TABLE (job_id uuid, org_id uuid, investigation_id uuid)
    LANGUAGE sql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    UPDATE aegis.investigation_jobs j
       SET status = 'dead', updated_at = now(), last_error = coalesce(j.last_error, 'lease expired')
     WHERE j.status = 'running' AND j.lease_expires_at < now() AND j.attempts >= j.max_attempts
    RETURNING j.id, j.org_id, j.investigation_id
$$;
REVOKE INSERT ON aegis.investigation_events FROM aegis_definer;
REVOKE SELECT, UPDATE ON aegis.investigations FROM aegis_definer;

-- restore the 0001 claim function (per-row running count, no priority)
CREATE OR REPLACE FUNCTION aegis.claim_job(p_worker text, p_lease_seconds integer, p_org_cap integer)
RETURNS TABLE (job_id uuid, org_id uuid, investigation_id uuid, attempt integer)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
DECLARE
    picked record;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('aegis-claim', 0));
    SELECT j.id, j.org_id, j.investigation_id INTO picked
      FROM aegis.investigation_jobs j
     WHERE ((j.status = 'queued' AND j.available_at <= now())
            OR (j.status = 'running' AND j.lease_expires_at < now()))
       AND j.attempts < j.max_attempts
       AND (SELECT count(*) FROM aegis.investigation_jobs x
             WHERE x.org_id = j.org_id AND x.status = 'running' AND x.lease_expires_at >= now())
           < p_org_cap
     ORDER BY (SELECT count(*) FROM aegis.investigation_jobs x
                WHERE x.org_id = j.org_id AND x.status = 'running' AND x.lease_expires_at >= now()),
              j.available_at, j.id
     LIMIT 1
       FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    RETURN QUERY
    UPDATE aegis.investigation_jobs j
       SET status = 'running', attempts = j.attempts + 1, lease_owner = p_worker,
           lease_expires_at = now() + make_interval(secs => p_lease_seconds), updated_at = now()
     WHERE j.id = picked.id
    RETURNING j.id, j.org_id, j.investigation_id, j.attempts;
END $$;

DROP INDEX IF EXISTS aegis.investigation_jobs_claim_idx;
CREATE INDEX investigation_jobs_claim_idx ON aegis.investigation_jobs (status, available_at);
ALTER TABLE aegis.investigation_jobs DROP COLUMN IF EXISTS priority;
DELETE FROM aegis.investigation_jobs WHERE status = 'deferred';
ALTER TABLE aegis.investigation_jobs DROP CONSTRAINT investigation_jobs_status_check;
ALTER TABLE aegis.investigation_jobs ADD CONSTRAINT investigation_jobs_status_check
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'dead', 'cancelled'));

ALTER TABLE aegis.investigations DROP COLUMN IF EXISTS event_seq;

DROP TABLE IF EXISTS aegis.change_events, aegis.alerts, aegis.invitations, aegis.api_keys;

DROP INDEX IF EXISTS aegis.incidents_org_severity_idx;
DROP INDEX IF EXISTS aegis.incidents_org_service_idx;
DROP INDEX IF EXISTS aegis.incidents_open_group_idx;
ALTER TABLE aegis.incidents
    DROP COLUMN IF EXISTS auto_investigated_at,
    DROP COLUMN IF EXISTS alert_count,
    DROP COLUMN IF EXISTS last_alert_at;
ALTER TABLE aegis.incidents RENAME COLUMN group_key TO fingerprint;
