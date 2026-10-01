-- Migration 0002: API keys, invitations, alert ingestion and grouping, change events,
-- queue deferral/priority, per-investigation event sequencing (ADR-010, ADR-011, ADR-013).
-- Runs as aegis_owner with search_path = aegis.

-- ---------------------------------------------------------------------------------------
-- API keys (ADR-011): aegis_<prefix>_<secret>. Only an HMAC-SHA-256 of the full key (server
-- pepper) is stored; the prefix identifies the key for humans and logs. Scopes are a closed
-- set and never include administrative rights.
-- ---------------------------------------------------------------------------------------
CREATE TABLE aegis.api_keys (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL REFERENCES aegis.organizations(id) ON DELETE CASCADE,
    name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    prefix          text NOT NULL UNIQUE CHECK (prefix ~ '^[a-z2-7]{8}$'),
    secret_hash     bytea NOT NULL UNIQUE CHECK (length(secret_hash) = 32),
    scopes          text[] NOT NULL CHECK (
                        cardinality(scopes) BETWEEN 1 AND 3
                        AND scopes <@ ARRAY['ingest:alerts', 'ingest:changes', 'read']::text[]),
    created_by      uuid REFERENCES aegis.users(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    last_used_at    timestamptz,
    revoked_at      timestamptz,
    revoked_reason  text CHECK (length(revoked_reason) <= 200),
    rotated_from    uuid,
    CHECK (expires_at > created_at),
    UNIQUE (org_id, id),
    FOREIGN KEY (org_id, rotated_from) REFERENCES aegis.api_keys (org_id, id)
        ON DELETE SET NULL (rotated_from)
);
CREATE INDEX api_keys_org_idx ON aegis.api_keys (org_id, created_at DESC);
CREATE INDEX api_keys_creator_idx ON aegis.api_keys (org_id, created_by);

-- ---------------------------------------------------------------------------------------
-- Invitations (ADR-011 amendment): the only way a user gains a membership besides operator
-- tooling. Accepted at login only when the IdP reports the email as verified.
-- ---------------------------------------------------------------------------------------
CREATE TABLE aegis.invitations (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL REFERENCES aegis.organizations(id) ON DELETE CASCADE,
    email        text NOT NULL CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 320),
    role         text NOT NULL CHECK (role IN ('admin', 'investigator', 'reviewer', 'viewer')),
    invited_by   uuid REFERENCES aegis.users(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    accepted_at  timestamptz,
    accepted_by  uuid REFERENCES aegis.users(id) ON DELETE SET NULL,
    revoked_at   timestamptz,
    UNIQUE (org_id, id)
);
CREATE UNIQUE INDEX invitations_open_idx ON aegis.invitations (org_id, email)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

-- ---------------------------------------------------------------------------------------
-- Incident grouping. An incident with a non-NULL group_key is "open for grouping": alerts of
-- the same (service, environment) attach to it. The partial unique index guarantees at most
-- one such incident per group even under concurrent ingestion; grouping closes by setting
-- group_key to NULL (window expired, incident resolved).
-- ---------------------------------------------------------------------------------------
ALTER TABLE aegis.incidents RENAME COLUMN fingerprint TO group_key;
ALTER TABLE aegis.incidents
    ADD COLUMN last_alert_at        timestamptz,
    ADD COLUMN alert_count          integer NOT NULL DEFAULT 0 CHECK (alert_count >= 0),
    ADD COLUMN auto_investigated_at timestamptz;
CREATE UNIQUE INDEX incidents_open_group_idx ON aegis.incidents (org_id, group_key)
    WHERE group_key IS NOT NULL;
CREATE INDEX incidents_org_service_idx ON aegis.incidents (org_id, service, created_at DESC);
CREATE INDEX incidents_org_severity_idx ON aegis.incidents (org_id, severity, created_at DESC);

-- ---------------------------------------------------------------------------------------
-- Alerts. One row per alert occurrence: (fingerprint, starts_at). Re-deliveries of the same
-- occurrence (Alertmanager re-sends every group/repeat interval) update the row instead of
-- creating new ones.
-- ---------------------------------------------------------------------------------------
CREATE TABLE aegis.alerts (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             uuid NOT NULL,
    incident_id        uuid NOT NULL,
    source             text NOT NULL CHECK (source IN ('alertmanager', 'webhook')),
    fingerprint        text NOT NULL CHECK (length(fingerprint) BETWEEN 1 AND 128),
    status             text NOT NULL CHECK (status IN ('firing', 'resolved')),
    name               text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    severity           text NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    service            text CHECK (length(service) <= 200),
    environment        text CHECK (length(environment) <= 100),
    labels             jsonb NOT NULL DEFAULT '{}'::jsonb,
    annotations        jsonb NOT NULL DEFAULT '{}'::jsonb,
    generator_url      text CHECK (length(generator_url) <= 2000 AND generator_url ~ '^https?://'),
    starts_at          timestamptz NOT NULL,
    ends_at            timestamptz,
    first_received_at  timestamptz NOT NULL DEFAULT now(),
    last_received_at   timestamptz NOT NULL DEFAULT now(),
    receive_count      integer NOT NULL DEFAULT 1 CHECK (receive_count >= 1),
    received_via       uuid,
    UNIQUE (org_id, fingerprint, starts_at),
    UNIQUE (org_id, id),
    FOREIGN KEY (org_id, incident_id) REFERENCES aegis.incidents (org_id, id) ON DELETE CASCADE
);
CREATE INDEX alerts_incident_idx ON aegis.alerts (org_id, incident_id, starts_at DESC);

-- ---------------------------------------------------------------------------------------
-- Change events (ADR-013): evidence, never proof of causality.
-- ---------------------------------------------------------------------------------------
CREATE TABLE aegis.change_events (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES aegis.organizations(id) ON DELETE CASCADE,
    source        text NOT NULL CHECK (length(source) BETWEEN 1 AND 100),
    external_id   text CHECK (length(external_id) BETWEEN 1 AND 200),
    kind          text NOT NULL CHECK (kind IN ('deployment', 'config', 'feature_flag', 'rollout',
                                                'infrastructure', 'other')),
    service       text CHECK (length(service) <= 200),
    environment   text CHECK (length(environment) <= 100),
    version       text CHECK (length(version) <= 200),
    summary       text NOT NULL CHECK (length(summary) BETWEEN 1 AND 2000),
    actor         text CHECK (length(actor) <= 200),
    ts            timestamptz NOT NULL,
    received_at   timestamptz NOT NULL DEFAULT now(),
    scope         jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    received_via  uuid,
    UNIQUE (org_id, source, external_id),
    UNIQUE (org_id, id)
);
CREATE INDEX change_events_time_idx ON aegis.change_events (org_id, ts DESC);
CREATE INDEX change_events_service_idx ON aegis.change_events (org_id, service, ts DESC);

-- ---------------------------------------------------------------------------------------
-- Investigations: per-investigation event sequence counter. Appending an event increments it
-- with a row lock, so concurrent writers (worker, API cancel) never collide on seq.
-- ---------------------------------------------------------------------------------------
ALTER TABLE aegis.investigations ADD COLUMN event_seq integer NOT NULL DEFAULT 0;

-- ---------------------------------------------------------------------------------------
-- Jobs: 'deferred' (queue over the organization's limit: recorded, never dropped) and a
-- priority (manual starts before automatic ones within an organization).
-- ---------------------------------------------------------------------------------------
ALTER TABLE aegis.investigation_jobs DROP CONSTRAINT investigation_jobs_status_check;
ALTER TABLE aegis.investigation_jobs ADD CONSTRAINT investigation_jobs_status_check
    CHECK (status IN ('deferred', 'queued', 'running', 'succeeded', 'failed', 'dead', 'cancelled'));
ALTER TABLE aegis.investigation_jobs ADD COLUMN priority smallint NOT NULL DEFAULT 0;
DROP INDEX aegis.investigation_jobs_claim_idx;
CREATE INDEX investigation_jobs_claim_idx ON aegis.investigation_jobs (status, available_at)
    WHERE status IN ('queued', 'running', 'deferred');

-- Claim: running counts per organization are computed once (CTE) instead of per row.
CREATE OR REPLACE FUNCTION aegis.claim_job(p_worker text, p_lease_seconds integer, p_org_cap integer)
RETURNS TABLE (job_id uuid, org_id uuid, investigation_id uuid, attempt integer)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
DECLARE
    picked uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('aegis-claim', 0));
    WITH running AS (
        SELECT x.org_id, count(*) AS n FROM aegis.investigation_jobs x
         WHERE x.status = 'running' AND x.lease_expires_at >= now()
         GROUP BY x.org_id)
    SELECT j.id INTO picked
      FROM aegis.investigation_jobs j
      LEFT JOIN running r ON r.org_id = j.org_id
     WHERE ((j.status = 'queued' AND j.available_at <= now())
            OR (j.status = 'running' AND j.lease_expires_at < now()))
       AND j.attempts < j.max_attempts
       AND coalesce(r.n, 0) < p_org_cap
     ORDER BY coalesce(r.n, 0), j.priority DESC, j.available_at, j.id
     LIMIT 1
       FOR UPDATE OF j SKIP LOCKED;
    IF picked IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    UPDATE aegis.investigation_jobs j
       SET status = 'running', attempts = j.attempts + 1, lease_owner = p_worker,
           lease_expires_at = now() + make_interval(secs => p_lease_seconds), updated_at = now()
     WHERE j.id = picked
    RETURNING j.id, j.org_id, j.investigation_id, j.attempts;
END $$;

-- Reaper: jobs whose lease expired with no attempts left become 'dead' and their
-- investigation 'failed' (with an event) in the same transaction, so a crash between the two
-- cannot leave an investigation running forever. Locks the investigation before the job
-- (the lock order used everywhere, see aegis/jobs/queue.py).
CREATE OR REPLACE FUNCTION aegis.reap_dead_jobs()
RETURNS TABLE (job_id uuid, org_id uuid, investigation_id uuid)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
DECLARE
    r record;
    s integer;
BEGIN
    FOR r IN SELECT j.id, j.org_id, j.investigation_id FROM aegis.investigation_jobs j
              WHERE j.status = 'running' AND j.lease_expires_at < now()
                AND j.attempts >= j.max_attempts
    LOOP
        PERFORM 1 FROM aegis.investigations i WHERE i.id = r.investigation_id FOR UPDATE;
        UPDATE aegis.investigation_jobs j
           SET status = 'dead', updated_at = now(),
               last_error = coalesce(j.last_error, 'lease expired with no attempts left')
         WHERE j.id = r.id AND j.status = 'running' AND j.lease_expires_at < now()
           AND j.attempts >= j.max_attempts;
        IF NOT FOUND THEN
            CONTINUE;
        END IF;
        s := NULL;
        UPDATE aegis.investigations i
           SET status = 'failed', finished_at = now(), event_seq = i.event_seq + 1,
               error = 'The worker lost its lease and no attempts remain'
         WHERE i.id = r.investigation_id AND i.status IN ('queued', 'running')
        RETURNING i.event_seq INTO s;
        IF s IS NOT NULL THEN
            INSERT INTO aegis.investigation_events
                (org_id, investigation_id, seq, kind, state, actor, message, data)
            VALUES (r.org_id, r.investigation_id, s, 'failed', 'failed', 'reaper',
                    'The worker lost its lease and no attempts remain', '{}'::jsonb);
        END IF;
        job_id := r.id;
        org_id := r.org_id;
        investigation_id := r.investigation_id;
        RETURN NEXT;
    END LOOP;
END $$;
GRANT SELECT, UPDATE ON aegis.investigations TO aegis_definer;
GRANT INSERT ON aegis.investigation_events TO aegis_definer;

-- Moves the oldest deferred jobs of each organization to 'queued' while that organization is
-- below its queue limit. Serialised so concurrent workers cannot overshoot the limit.
CREATE FUNCTION aegis.promote_deferred_jobs(p_queue_limit integer)
RETURNS TABLE (job_id uuid, org_id uuid, investigation_id uuid)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('aegis-promote', 0));
    RETURN QUERY
    WITH queued AS (
        SELECT q.org_id, count(*) AS n FROM aegis.investigation_jobs q
         WHERE q.status = 'queued' GROUP BY q.org_id),
    ranked AS (
        SELECT d.id, d.org_id,
               row_number() OVER (PARTITION BY d.org_id ORDER BY d.priority DESC, d.created_at, d.id) AS rn
          FROM aegis.investigation_jobs d WHERE d.status = 'deferred')
    UPDATE aegis.investigation_jobs j
       SET status = 'queued', available_at = now(), updated_at = now()
      FROM ranked r LEFT JOIN queued q ON q.org_id = r.org_id
     WHERE j.id = r.id AND r.rn <= p_queue_limit - coalesce(q.n, 0)
    RETURNING j.id, j.org_id, j.investigation_id;
END $$;

-- ---------------------------------------------------------------------------------------
-- Pre-organization lookups (SECURITY DEFINER, BYPASSRLS owner, pinned search_path).
-- ---------------------------------------------------------------------------------------
CREATE FUNCTION aegis.api_key_lookup(p_hash bytea)
RETURNS TABLE (key_id uuid, org_id uuid, name text, prefix text, scopes text[],
               expires_at timestamptz, revoked boolean)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    SELECT k.id, k.org_id, k.name, k.prefix, k.scopes, k.expires_at, k.revoked_at IS NOT NULL
      FROM aegis.api_keys k
     WHERE k.secret_hash = p_hash
$$;

-- Open, unexpired invitations for a (verified) email address.
CREATE FUNCTION aegis.invitations_for_email(p_email text)
RETURNS TABLE (invitation_id uuid, org_id uuid, role text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    SELECT i.id, i.org_id, i.role
      FROM aegis.invitations i
     WHERE i.email = lower(p_email) AND i.accepted_at IS NULL AND i.revoked_at IS NULL
       AND i.expires_at > now()
     ORDER BY i.created_at
$$;

-- ---------------------------------------------------------------------------------------
-- RLS on the new tenant tables.
-- ---------------------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['api_keys', 'invitations', 'alerts', 'change_events']
    LOOP
        EXECUTE format('ALTER TABLE aegis.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE aegis.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON aegis.%I USING (org_id = aegis.current_org()) '
                       'WITH CHECK (org_id = aegis.current_org())', t);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------------------
-- Grants (least privilege). No DELETE anywhere new: keys and invitations are revoked, alerts
-- and change events are removed only by retention (a later migration grants that narrowly).
-- ---------------------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON aegis.api_keys, aegis.invitations, aegis.alerts TO aegis_app;
GRANT SELECT, INSERT ON aegis.change_events TO aegis_app;
GRANT SELECT ON aegis.api_keys, aegis.invitations TO aegis_definer;

GRANT CREATE ON SCHEMA aegis TO aegis_definer;
DO $$
DECLARE f text;
BEGIN
    FOREACH f IN ARRAY ARRAY[
        'aegis.api_key_lookup(bytea)',
        'aegis.invitations_for_email(text)',
        'aegis.promote_deferred_jobs(integer)',
        'aegis.claim_job(text, integer, integer)',
        'aegis.reap_dead_jobs()'
    ]
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO aegis_definer', f);
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO aegis_app', f);
    END LOOP;
END $$;
REVOKE CREATE ON SCHEMA aegis FROM aegis_definer;
