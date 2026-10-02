-- Migration 0006: LLM call records (data-egress evidence) and the global LLM slot pool.

-- One row per model call: which provider/model saw this organization's (redacted) data,
-- when, under which policy, and what came back (status only; prompts are not stored, only
-- their SHA-256 and size).
CREATE TABLE aegis.llm_calls (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             uuid NOT NULL,
    investigation_id   uuid NOT NULL,
    integration_id     uuid,
    connector_id       text NOT NULL CHECK (length(connector_id) <= 63),
    api                text NOT NULL CHECK (api IN ('ollama', 'openai', 'anthropic', 'fake')),
    model              text NOT NULL CHECK (length(model) <= 200),
    locality           text NOT NULL CHECK (locality IN ('local', 'cloud')),
    purpose            text NOT NULL DEFAULT 'adjudication' CHECK (length(purpose) <= 50),
    attempt            text NOT NULL CHECK (attempt IN ('initial', 'repair')),
    started_at         timestamptz NOT NULL DEFAULT now(),
    latency_ms         integer CHECK (latency_ms >= 0),
    prompt_tokens      integer CHECK (prompt_tokens >= 0),
    completion_tokens  integer CHECK (completion_tokens >= 0),
    status             text NOT NULL CHECK (status IN ('ok', 'invalid', 'error', 'timeout', 'unavailable')),
    error_category     text CHECK (length(error_category) <= 100),
    prompt_sha256      text CHECK (length(prompt_sha256) = 64),
    prompt_bytes       integer CHECK (prompt_bytes >= 0),
    redaction          jsonb NOT NULL DEFAULT '{}'::jsonb,
    policy             jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (org_id, id),
    FOREIGN KEY (org_id, investigation_id) REFERENCES aegis.investigations (org_id, id) ON DELETE CASCADE
);
CREATE INDEX llm_calls_org_time_idx ON aegis.llm_calls (org_id, started_at DESC);
CREATE INDEX llm_calls_investigation_idx ON aegis.llm_calls (org_id, investigation_id);
ALTER TABLE aegis.llm_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE aegis.llm_calls FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON aegis.llm_calls
    USING (org_id = aegis.current_org()) WITH CHECK (org_id = aegis.current_org());
GRANT SELECT, INSERT ON aegis.llm_calls TO aegis_app;

-- Global slot pool bounding concurrent model calls across all workers (ADR-010). Not tenant
-- data; reachable only through the definer functions below.
CREATE TABLE aegis.llm_slots (
    slot              integer PRIMARY KEY CHECK (slot BETWEEN 1 AND 64),
    holder            text CHECK (length(holder) <= 200),
    lease_expires_at  timestamptz
);
INSERT INTO aegis.llm_slots (slot) SELECT g FROM generate_series(1, 64) AS g;
GRANT SELECT, UPDATE ON aegis.llm_slots TO aegis_definer;

-- Takes the first free (or expired) slot among 1..p_slots; NULL when all are busy.
CREATE FUNCTION aegis.acquire_llm_slot(p_holder text, p_lease_seconds integer, p_slots integer)
RETURNS integer
    LANGUAGE sql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    UPDATE aegis.llm_slots s
       SET holder = p_holder, lease_expires_at = now() + make_interval(secs => p_lease_seconds)
     WHERE s.slot = (
        SELECT f.slot FROM aegis.llm_slots f
         WHERE f.slot <= least(greatest(p_slots, 1), 64)
           AND (f.holder IS NULL OR f.lease_expires_at < now())
         ORDER BY f.slot
         LIMIT 1
           FOR UPDATE SKIP LOCKED)
    RETURNING s.slot
$$;

CREATE FUNCTION aegis.renew_llm_slot(p_slot integer, p_holder text, p_lease_seconds integer)
RETURNS boolean
    LANGUAGE sql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    WITH u AS (
        UPDATE aegis.llm_slots s SET lease_expires_at = now() + make_interval(secs => p_lease_seconds)
         WHERE s.slot = p_slot AND s.holder = p_holder RETURNING 1)
    SELECT EXISTS (SELECT 1 FROM u)
$$;

CREATE FUNCTION aegis.release_llm_slot(p_slot integer, p_holder text)
RETURNS void
    LANGUAGE sql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    UPDATE aegis.llm_slots s SET holder = NULL, lease_expires_at = NULL
     WHERE s.slot = p_slot AND s.holder = p_holder
$$;

GRANT CREATE ON SCHEMA aegis TO aegis_definer;
DO $$
DECLARE f text;
BEGIN
    FOREACH f IN ARRAY ARRAY[
        'aegis.acquire_llm_slot(text, integer, integer)',
        'aegis.renew_llm_slot(integer, text, integer)',
        'aegis.release_llm_slot(integer, text)'
    ]
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO aegis_definer', f);
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO aegis_app', f);
    END LOOP;
END $$;
REVOKE CREATE ON SCHEMA aegis FROM aegis_definer;
