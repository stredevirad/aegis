-- Migration 0001: core schema for the vertical slice (ADR-010, ADR-012).
-- Runs as aegis_owner with search_path = aegis.

-- ---------------------------------------------------------------------------------------
-- Tenant context helpers. NULLIF: a pooled connection without SET LOCAL reads '' -> NULL,
-- which matches no row (fail closed).
-- ---------------------------------------------------------------------------------------
CREATE FUNCTION aegis.current_org() RETURNS uuid
    LANGUAGE sql STABLE
    SET search_path = pg_catalog, pg_temp
AS $$ SELECT NULLIF(current_setting('aegis.org_id', true), '')::uuid $$;

CREATE FUNCTION aegis.current_user_id() RETURNS uuid
    LANGUAGE sql STABLE
    SET search_path = pg_catalog, pg_temp
AS $$ SELECT NULLIF(current_setting('aegis.user_id', true), '')::uuid $$;

-- ---------------------------------------------------------------------------------------
-- Identity and tenancy
-- ---------------------------------------------------------------------------------------
CREATE TABLE aegis.organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    settings    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE aegis.users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issuer          text NOT NULL CHECK (length(issuer) <= 500),
    subject         text NOT NULL CHECK (length(subject) <= 500),
    email           text CHECK (length(email) <= 320),
    email_verified  boolean NOT NULL DEFAULT false,
    display_name    text CHECK (length(display_name) <= 200),
    disabled        boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

CREATE TABLE aegis.memberships (
    org_id      uuid NOT NULL REFERENCES aegis.organizations(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES aegis.users(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('admin', 'investigator', 'reviewer', 'viewer')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);

CREATE TABLE aegis.sessions (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash       bytea NOT NULL UNIQUE,
    org_id           uuid NOT NULL REFERENCES aegis.organizations(id) ON DELETE CASCADE,
    user_id          uuid NOT NULL REFERENCES aegis.users(id) ON DELETE CASCADE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    idle_expires_at  timestamptz NOT NULL,
    expires_at       timestamptz NOT NULL,
    revoked_at       timestamptz,
    user_agent       text CHECK (length(user_agent) <= 500)
);
CREATE INDEX sessions_user_idx ON aegis.sessions (org_id, user_id);

-- ---------------------------------------------------------------------------------------
-- Incidents and investigations
-- ---------------------------------------------------------------------------------------
CREATE TABLE aegis.incidents (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         uuid NOT NULL REFERENCES aegis.organizations(id) ON DELETE CASCADE,
    title          text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    description    text NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
    severity       text NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    status         text NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open', 'investigating', 'resolved', 'closed')),
    service        text CHECK (length(service) <= 200),
    environment    text CHECK (length(environment) <= 100),
    source         text NOT NULL DEFAULT 'manual' CHECK (length(source) <= 100),
    fingerprint    text CHECK (length(fingerprint) <= 200),
    labels         jsonb NOT NULL DEFAULT '{}'::jsonb,
    annotations    jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at     timestamptz NOT NULL,
    resolved_at    timestamptz,
    created_by     text CHECK (length(created_by) <= 200),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, id)
);
CREATE INDEX incidents_org_created_idx ON aegis.incidents (org_id, created_at DESC, id DESC);
CREATE INDEX incidents_org_status_idx ON aegis.incidents (org_id, status);

CREATE TABLE aegis.investigations (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            uuid NOT NULL,
    incident_id       uuid NOT NULL,
    status            text NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    trigger           text NOT NULL DEFAULT 'manual' CHECK (trigger IN ('manual', 'auto', 'replay')),
    requested_by      text CHECK (length(requested_by) <= 200),
    idempotency_key   text CHECK (length(idempotency_key) <= 200),
    window_start      timestamptz NOT NULL,
    window_end        timestamptz NOT NULL,
    baseline_start    timestamptz NOT NULL,
    baseline_end      timestamptz NOT NULL,
    source            jsonb NOT NULL,
    classification    text CHECK (classification IN ('CONFIRMED', 'PLAUSIBLE', 'INSUFFICIENT', 'NO_ANOMALY')),
    support           text CHECK (support IN ('strong', 'moderate', 'weak', 'none')),
    display_label     text,
    summary           text,
    verdict           jsonb,
    error             text CHECK (length(error) <= 2000),
    cancel_requested  boolean NOT NULL DEFAULT false,
    created_at        timestamptz NOT NULL DEFAULT now(),
    started_at        timestamptz,
    finished_at       timestamptz,
    CHECK (window_end > window_start AND baseline_end <= window_start),
    UNIQUE (org_id, id),
    UNIQUE (org_id, idempotency_key),
    FOREIGN KEY (org_id, incident_id) REFERENCES aegis.incidents (org_id, id) ON DELETE CASCADE
);
-- at most one active investigation per incident (idempotent start, ADR-010)
CREATE UNIQUE INDEX investigations_one_active_idx
    ON aegis.investigations (incident_id) WHERE status IN ('queued', 'running');
CREATE INDEX investigations_incident_idx ON aegis.investigations (org_id, incident_id, created_at DESC);

CREATE TABLE aegis.investigation_jobs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            uuid NOT NULL,
    investigation_id  uuid NOT NULL UNIQUE,
    status            text NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'dead', 'cancelled')),
    attempts          integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts      integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    available_at      timestamptz NOT NULL DEFAULT now(),
    lease_owner       text,
    lease_expires_at  timestamptz,
    last_error        text CHECK (length(last_error) <= 2000),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (org_id, investigation_id) REFERENCES aegis.investigations (org_id, id) ON DELETE CASCADE
);
CREATE INDEX investigation_jobs_claim_idx ON aegis.investigation_jobs (status, available_at);
CREATE INDEX investigation_jobs_org_idx ON aegis.investigation_jobs (org_id, status);

CREATE TABLE aegis.investigation_events (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id            uuid NOT NULL,
    investigation_id  uuid NOT NULL,
    seq               integer NOT NULL CHECK (seq > 0),
    ts                timestamptz NOT NULL DEFAULT clock_timestamp(),
    kind              text NOT NULL CHECK (length(kind) <= 50),
    state             text CHECK (length(state) <= 50),
    actor             text CHECK (length(actor) <= 200),
    message           text NOT NULL CHECK (length(message) <= 2000),
    data              jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (investigation_id, seq),
    FOREIGN KEY (org_id, investigation_id) REFERENCES aegis.investigations (org_id, id) ON DELETE CASCADE
);

CREATE TABLE aegis.snapshots (
    org_id            uuid NOT NULL,
    investigation_id  uuid NOT NULL,
    schema_version    text NOT NULL,
    digest            text NOT NULL,
    synthetic         boolean NOT NULL,
    payload           jsonb NOT NULL,
    redaction         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz,
    PRIMARY KEY (org_id, investigation_id),
    FOREIGN KEY (org_id, investigation_id) REFERENCES aegis.investigations (org_id, id) ON DELETE CASCADE
);

-- Evidence is immutable and keyed per (org, investigation, evidence): the same trace seen by
-- two investigations is two rows and can never be re-homed (audit DATA-01 / S-C1).
CREATE TABLE aegis.evidence (
    org_id            uuid NOT NULL,
    investigation_id  uuid NOT NULL,
    evidence_id       text NOT NULL CHECK (length(evidence_id) <= 100),
    kind              text NOT NULL,
    modality          text,
    service           text,
    observed_at       timestamptz,
    summary           text NOT NULL,
    attributes        jsonb NOT NULL DEFAULT '{}'::jsonb,
    sources           jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (org_id, investigation_id, evidence_id),
    FOREIGN KEY (org_id, investigation_id) REFERENCES aegis.investigations (org_id, id) ON DELETE CASCADE
);
CREATE INDEX evidence_filter_idx ON aegis.evidence (org_id, investigation_id, kind, service);

CREATE TABLE aegis.evidence_relations (
    org_id            uuid NOT NULL,
    investigation_id  uuid NOT NULL,
    source            text NOT NULL,
    target            text NOT NULL,
    kind              text NOT NULL,
    PRIMARY KEY (org_id, investigation_id, source, target, kind),
    FOREIGN KEY (org_id, investigation_id) REFERENCES aegis.investigations (org_id, id) ON DELETE CASCADE
);

CREATE TABLE aegis.candidates (
    org_id            uuid NOT NULL,
    investigation_id  uuid NOT NULL,
    service           text NOT NULL,
    rank              integer NOT NULL,
    rule              text,
    rule_sentence     text,
    hypothesis        text NOT NULL,
    inferred          boolean NOT NULL DEFAULT false,
    features          jsonb NOT NULL,
    checks            jsonb NOT NULL,
    supporting        text[] NOT NULL DEFAULT '{}',
    contradicting     text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (org_id, investigation_id, service),
    FOREIGN KEY (org_id, investigation_id) REFERENCES aegis.investigations (org_id, id) ON DELETE CASCADE
);

CREATE TABLE aegis.root_causes (
    org_id            uuid NOT NULL,
    investigation_id  uuid NOT NULL,
    service           text NOT NULL,
    mechanism         text NOT NULL,
    support           text NOT NULL,
    supporting        text[] NOT NULL DEFAULT '{}',
    contradicting     text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (org_id, investigation_id, service),
    FOREIGN KEY (org_id, investigation_id) REFERENCES aegis.investigations (org_id, id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------------------
-- Append-only, hash-chained audit log. The application role may only INSERT and SELECT.
-- The chain detects application-level and accidental tampering; it is NOT tamper-evident
-- against the database owner (docs/security-model.md, trust boundaries).
-- ---------------------------------------------------------------------------------------
CREATE TABLE aegis.audit_events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id       uuid NOT NULL REFERENCES aegis.organizations(id) ON DELETE CASCADE,
    seq          bigint NOT NULL,
    ts           timestamptz NOT NULL,
    actor_type   text NOT NULL CHECK (actor_type IN ('user', 'api_key', 'system')),
    actor_id     text CHECK (length(actor_id) <= 200),
    action       text NOT NULL CHECK (length(action) <= 100),
    target_type  text CHECK (length(target_type) <= 100),
    target_id    text CHECK (length(target_id) <= 200),
    details      jsonb NOT NULL DEFAULT '{}'::jsonb,
    request_id   text CHECK (length(request_id) <= 100),
    prev_hash    bytea NOT NULL,
    hash         bytea NOT NULL,
    UNIQUE (org_id, seq)
);

CREATE FUNCTION aegis.audit_hash(
    p_org uuid, p_seq bigint, p_ts timestamptz, p_actor_type text, p_actor_id text,
    p_action text, p_target_type text, p_target_id text, p_details jsonb, p_request_id text,
    p_prev bytea
) RETURNS bytea
    LANGUAGE sql STABLE
    SET search_path = pg_catalog, pg_temp
AS $$
    -- Explicit UTC epoch microseconds and canonical jsonb text: the result does not depend on
    -- TimeZone, DateStyle or float output settings.
    SELECT sha256(convert_to(concat_ws(chr(31),
        p_org::text,
        p_seq::text,
        (extract(epoch FROM p_ts) * 1000000)::bigint::text,
        p_actor_type,
        coalesce(p_actor_id, ''),
        p_action,
        coalesce(p_target_type, ''),
        coalesce(p_target_id, ''),
        coalesce(p_details::text, 'null'),
        coalesce(p_request_id, ''),
        encode(p_prev, 'hex')
    ), 'UTF8'))
$$;

CREATE FUNCTION aegis.audit_chain() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
DECLARE
    last_hash bytea;
    last_seq bigint;
BEGIN
    -- serialise appends per organization so the chain has no forks
    PERFORM pg_advisory_xact_lock(hashtextextended('aegis-audit:' || NEW.org_id::text, 0));
    SELECT a.hash, a.seq INTO last_hash, last_seq
      FROM aegis.audit_events a WHERE a.org_id = NEW.org_id ORDER BY a.seq DESC LIMIT 1;
    NEW.seq := coalesce(last_seq, 0) + 1;
    NEW.prev_hash := coalesce(last_hash, sha256(convert_to('aegis-genesis:' || NEW.org_id::text, 'UTF8')));
    NEW.ts := clock_timestamp();
    NEW.hash := aegis.audit_hash(NEW.org_id, NEW.seq, NEW.ts, NEW.actor_type, NEW.actor_id,
        NEW.action, NEW.target_type, NEW.target_id, NEW.details, NEW.request_id, NEW.prev_hash);
    RETURN NEW;
END $$;

CREATE TRIGGER audit_events_chain BEFORE INSERT ON aegis.audit_events
    FOR EACH ROW EXECUTE FUNCTION aegis.audit_chain();

-- Returns the first sequence number whose hash or link does not verify (NULL = intact) for the
-- current organization. Uses the same audit_hash() as the trigger.
CREATE FUNCTION aegis.audit_verify() RETURNS bigint
    LANGUAGE plpgsql STABLE
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
DECLARE
    r record;
    expected_prev bytea;
BEGIN
    expected_prev := sha256(convert_to('aegis-genesis:' || aegis.current_org()::text, 'UTF8'));
    FOR r IN SELECT * FROM aegis.audit_events WHERE org_id = aegis.current_org() ORDER BY seq LOOP
        IF r.prev_hash <> expected_prev OR r.hash <> aegis.audit_hash(r.org_id, r.seq, r.ts,
            r.actor_type, r.actor_id, r.action, r.target_type, r.target_id, r.details,
            r.request_id, r.prev_hash) THEN
            RETURN r.seq;
        END IF;
        expected_prev := r.hash;
    END LOOP;
    RETURN NULL;
END $$;

-- ---------------------------------------------------------------------------------------
-- Row-level security: every tenant table, enabled AND forced.
-- ---------------------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['memberships', 'sessions', 'incidents', 'investigations',
        'investigation_jobs', 'investigation_events', 'snapshots', 'evidence',
        'evidence_relations', 'candidates', 'root_causes', 'audit_events']
    LOOP
        EXECUTE format('ALTER TABLE aegis.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE aegis.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON aegis.%I USING (org_id = aegis.current_org()) '
                       'WITH CHECK (org_id = aegis.current_org())', t);
    END LOOP;
END $$;

ALTER TABLE aegis.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE aegis.organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON aegis.organizations
    USING (id = aegis.current_org()) WITH CHECK (id = aegis.current_org());

-- Users are not tenant-owned, but other tenants' users (and their emails) must not be visible:
-- a user row is visible only to itself and to organizations it is a member of.
ALTER TABLE aegis.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE aegis.users FORCE ROW LEVEL SECURITY;
CREATE POLICY user_visibility ON aegis.users
    USING (id = aegis.current_user_id() OR EXISTS (
        SELECT 1 FROM aegis.memberships m
        WHERE m.user_id = users.id AND m.org_id = aegis.current_org()));

-- ---------------------------------------------------------------------------------------
-- Grants: least privilege for the application role.
-- ---------------------------------------------------------------------------------------
GRANT EXECUTE ON FUNCTION aegis.current_org(), aegis.current_user_id(), aegis.audit_verify(),
    aegis.audit_hash(uuid, bigint, timestamptz, text, text, text, text, text, jsonb, text, bytea)
    TO aegis_app;
GRANT SELECT, UPDATE ON aegis.organizations TO aegis_app;
GRANT SELECT ON aegis.users TO aegis_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON aegis.memberships, aegis.sessions TO aegis_app;
GRANT SELECT, INSERT, UPDATE ON aegis.incidents, aegis.investigations, aegis.investigation_jobs TO aegis_app;
GRANT SELECT, INSERT ON aegis.investigation_events TO aegis_app;
GRANT SELECT, INSERT, DELETE ON aegis.snapshots, aegis.evidence, aegis.evidence_relations,
    aegis.candidates, aegis.root_causes TO aegis_app;
GRANT SELECT, INSERT ON aegis.audit_events TO aegis_app;  -- no UPDATE, no DELETE

-- ---------------------------------------------------------------------------------------
-- SECURITY DEFINER functions (owned by aegis_definer, which has BYPASSRLS). Each returns only
-- what its caller needs; search_path is pinned; PUBLIC cannot execute them.
-- ---------------------------------------------------------------------------------------
GRANT SELECT ON aegis.sessions, aegis.users, aegis.memberships, aegis.organizations TO aegis_definer;
GRANT INSERT, UPDATE ON aegis.users TO aegis_definer;
GRANT SELECT, UPDATE ON aegis.investigation_jobs TO aegis_definer;
GRANT EXECUTE ON FUNCTION aegis.current_org() TO aegis_definer;

CREATE FUNCTION aegis.session_lookup(p_token_hash bytea)
RETURNS TABLE (session_id uuid, org_id uuid, user_id uuid, role text, idle_expires_at timestamptz,
               expires_at timestamptz, revoked boolean, user_disabled boolean)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    -- role comes from the current membership, so removing a membership invalidates the session
    SELECT s.id, s.org_id, s.user_id, m.role, s.idle_expires_at, s.expires_at,
           s.revoked_at IS NOT NULL, u.disabled
      FROM aegis.sessions s
      JOIN aegis.users u ON u.id = s.user_id
      LEFT JOIN aegis.memberships m ON m.org_id = s.org_id AND m.user_id = s.user_id
     WHERE s.token_hash = p_token_hash
$$;

CREATE FUNCTION aegis.user_upsert(p_issuer text, p_subject text, p_email text,
                                  p_email_verified boolean, p_display_name text)
RETURNS uuid
    LANGUAGE sql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    INSERT INTO aegis.users (issuer, subject, email, email_verified, display_name)
    VALUES (p_issuer, p_subject, p_email, p_email_verified, p_display_name)
    ON CONFLICT (issuer, subject) DO UPDATE
        SET email = EXCLUDED.email, email_verified = EXCLUDED.email_verified,
            display_name = EXCLUDED.display_name
    RETURNING id
$$;

CREATE FUNCTION aegis.user_memberships(p_user uuid)
RETURNS TABLE (org_id uuid, slug text, name text, role text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    SELECT o.id, o.slug, o.name, m.role
      FROM aegis.memberships m JOIN aegis.organizations o ON o.id = m.org_id
     WHERE m.user_id = p_user ORDER BY o.slug
$$;

-- Claims the next runnable job. A global transaction-scoped advisory lock serialises claims so
-- the per-organization running cap cannot be exceeded by concurrent claimers (fairness, C20).
-- Expired leases are reclaimable; jobs whose attempts are exhausted are left for the reaper.
CREATE FUNCTION aegis.claim_job(p_worker text, p_lease_seconds integer, p_org_cap integer)
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

-- Jobs whose lease expired with no attempts left: marks them dead and returns them so the
-- worker can fail the investigation under the right organization context.
CREATE FUNCTION aegis.reap_dead_jobs()
RETURNS TABLE (job_id uuid, org_id uuid, investigation_id uuid)
    LANGUAGE sql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    UPDATE aegis.investigation_jobs j
       SET status = 'dead', updated_at = now(), last_error = coalesce(j.last_error, 'lease expired')
     WHERE j.status = 'running' AND j.lease_expires_at < now() AND j.attempts >= j.max_attempts
    RETURNING j.id, j.org_id, j.investigation_id
$$;

-- ALTER ... OWNER requires the new owner to hold CREATE on the schema: grant it only for the
-- duration of the ownership transfer.
GRANT CREATE ON SCHEMA aegis TO aegis_definer;
DO $$
DECLARE f text;
BEGIN
    FOREACH f IN ARRAY ARRAY[
        'aegis.session_lookup(bytea)',
        'aegis.user_upsert(text, text, text, boolean, text)',
        'aegis.user_memberships(uuid)',
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
