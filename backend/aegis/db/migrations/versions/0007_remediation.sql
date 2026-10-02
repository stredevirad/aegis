-- Migration 0007: human-gated remediation (ADR-014 + Council C amendment).
--
-- remediation_actions   organization-maintained catalog: exactly which flag may be set to
--                       which value. A model never chooses parameters.
-- remediation_proposals one proposed execution of a catalog action for an incident, with the
--                       target's prior state, the literal diff, the approval binding (hash of
--                       parameters + prior state + proposal, with expiry), execution,
--                       verification and rollback records.

CREATE TABLE aegis.remediation_actions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL REFERENCES aegis.organizations(id) ON DELETE CASCADE,
    name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    description     text NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    kind            text NOT NULL CHECK (kind IN ('flag_set')),
    integration_id  uuid NOT NULL,
    service         text CHECK (length(service) <= 200),
    flag_key        text NOT NULL CHECK (flag_key ~ '^[A-Za-z0-9._-]{1,100}$'),
    target_value    text NOT NULL CHECK (length(target_value) <= 100),
    risk            text NOT NULL CHECK (risk IN ('low', 'medium', 'high')),
    expected_impact text NOT NULL DEFAULT '' CHECK (length(expected_impact) <= 1000),
    enabled         boolean NOT NULL DEFAULT true,
    created_by      uuid REFERENCES aegis.users(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, id),
    UNIQUE (org_id, name),
    FOREIGN KEY (org_id, integration_id) REFERENCES aegis.integrations (org_id, id) ON DELETE CASCADE
);

CREATE TABLE aegis.remediation_proposals (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id               uuid NOT NULL,
    incident_id          uuid NOT NULL,
    investigation_id     uuid,
    action_id            uuid NOT NULL,
    status               text NOT NULL DEFAULT 'proposed' CHECK (status IN (
                             'proposed', 'approved', 'rejected', 'expired', 'executing', 'executed',
                             'failed', 'verifying', 'verified', 'verification_failed',
                             'rolling_back', 'rolled_back', 'rollback_failed')),
    params               jsonb NOT NULL,         -- {"flag_key": ..., "value": ...}
    inverse_params       jsonb NOT NULL,         -- {"flag_key": ..., "value": <prior value>}
    prior_state          jsonb NOT NULL,         -- {"value": ..., "version": n} read at proposal
    params_hash          text NOT NULL CHECK (length(params_hash) = 64),
    reason               text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
    evidence_ids         text[] NOT NULL DEFAULT '{}',
    risk                 text NOT NULL CHECK (risk IN ('low', 'medium', 'high')),
    expected_impact      text NOT NULL DEFAULT '',
    proposed_by          uuid REFERENCES aegis.users(id) ON DELETE SET NULL,
    proposed_at          timestamptz NOT NULL DEFAULT now(),
    approval_hash        text CHECK (length(approval_hash) = 64),
    approved_by          uuid REFERENCES aegis.users(id) ON DELETE SET NULL,
    approved_at          timestamptz,
    approval_expires_at  timestamptz,
    rejected_by          uuid REFERENCES aegis.users(id) ON DELETE SET NULL,
    rejected_reason      text CHECK (length(rejected_reason) <= 1000),
    executed_by          uuid REFERENCES aegis.users(id) ON DELETE SET NULL,
    executed_at          timestamptz,
    execution            jsonb NOT NULL DEFAULT '{}'::jsonb,
    verification         jsonb NOT NULL DEFAULT '{}'::jsonb,
    rolled_back_by       uuid REFERENCES aegis.users(id) ON DELETE SET NULL,
    rolled_back_at       timestamptz,
    rollback             jsonb NOT NULL DEFAULT '{}'::jsonb,
    verify_after         timestamptz,
    verify_lease_owner   text CHECK (length(verify_lease_owner) <= 200),
    verify_lease_until   timestamptz,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'approved' OR (approval_hash IS NOT NULL AND approval_expires_at IS NOT NULL)),
    UNIQUE (org_id, id),
    FOREIGN KEY (org_id, incident_id) REFERENCES aegis.incidents (org_id, id) ON DELETE CASCADE,
    FOREIGN KEY (org_id, investigation_id) REFERENCES aegis.investigations (org_id, id) ON DELETE SET NULL (investigation_id),
    FOREIGN KEY (org_id, action_id) REFERENCES aegis.remediation_actions (org_id, id) ON DELETE RESTRICT
);
-- one open proposal per action and incident (no duplicate concurrent proposals)
CREATE UNIQUE INDEX remediation_open_idx ON aegis.remediation_proposals (org_id, incident_id, action_id)
    WHERE status IN ('proposed', 'approved', 'executing', 'verifying', 'rolling_back');
CREATE INDEX remediation_incident_idx ON aegis.remediation_proposals (org_id, incident_id, proposed_at DESC);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['remediation_actions', 'remediation_proposals']
    LOOP
        EXECUTE format('ALTER TABLE aegis.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE aegis.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON aegis.%I USING (org_id = aegis.current_org()) '
                       'WITH CHECK (org_id = aegis.current_org())', t);
    END LOOP;
END $$;

-- Proposals are never deleted (they are part of the audit story); actions are disabled, and
-- deleted only when unused (RESTRICT above).
GRANT SELECT, INSERT, UPDATE, DELETE ON aegis.remediation_actions TO aegis_app;
GRANT SELECT, INSERT, UPDATE ON aegis.remediation_proposals TO aegis_app;

-- Post-action verification runs in the worker's maintenance loop once the system has had time
-- to settle. Claiming is cross-organization (pre-tenant), leased and skip-locked, so several
-- workers never verify the same proposal and a crashed worker's claim expires.
CREATE INDEX remediation_due_idx ON aegis.remediation_proposals (verify_after) WHERE status = 'verifying';
GRANT SELECT, UPDATE ON aegis.remediation_proposals TO aegis_definer;

CREATE FUNCTION aegis.claim_due_verification(p_worker text, p_lease_seconds integer)
RETURNS TABLE (proposal_id uuid, org_id uuid)
    LANGUAGE sql SECURITY DEFINER
    SET search_path = aegis, pg_catalog, pg_temp
AS $$
    UPDATE aegis.remediation_proposals p
       SET verify_lease_owner = p_worker,
           verify_lease_until = now() + make_interval(secs => p_lease_seconds)
     WHERE p.id = (
        SELECT d.id FROM aegis.remediation_proposals d
         WHERE d.status = 'verifying' AND d.verify_after <= now()
           AND (d.verify_lease_until IS NULL OR d.verify_lease_until < now())
         ORDER BY d.verify_after
         LIMIT 1
           FOR UPDATE SKIP LOCKED)
    RETURNING p.id, p.org_id
$$;

GRANT CREATE ON SCHEMA aegis TO aegis_definer;
ALTER FUNCTION aegis.claim_due_verification(text, integer) OWNER TO aegis_definer;
REVOKE ALL ON FUNCTION aegis.claim_due_verification(text, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION aegis.claim_due_verification(text, integer) TO aegis_app;
REVOKE CREATE ON SCHEMA aegis FROM aegis_definer;
