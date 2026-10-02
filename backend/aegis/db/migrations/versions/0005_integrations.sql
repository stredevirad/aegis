-- Migration 0005: tenant integrations referencing operator connectors (ADR-016).
-- Tenants never store URLs: connector_id names an entry of the operator's registry, which
-- the application re-checks (bound to this organization) on every use. Credentials are
-- AES-GCM ciphertexts whose associated data binds them to (org, connector, integration).
CREATE TABLE aegis.integrations (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             uuid NOT NULL REFERENCES aegis.organizations(id) ON DELETE CASCADE,
    connector_id       text NOT NULL CHECK (connector_id ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    kind               text NOT NULL CHECK (length(kind) BETWEEN 1 AND 30),
    name               text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    enabled            boolean NOT NULL DEFAULT true,
    credential_type    text NOT NULL DEFAULT 'none'
                       CHECK (credential_type IN ('none', 'bearer', 'basic', 'header')),
    credential_kid     text CHECK (length(credential_kid) <= 32),
    credential_blob    bytea CHECK (length(credential_blob) <= 8192),
    created_by         uuid REFERENCES aegis.users(id) ON DELETE SET NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    last_test_at       timestamptz,
    last_test_status   text CHECK (last_test_status IN ('ok', 'error')),
    last_test_detail   text CHECK (length(last_test_detail) <= 500),
    CHECK ((credential_type = 'none') = (credential_blob IS NULL)),
    CHECK ((credential_blob IS NULL) = (credential_kid IS NULL)),
    UNIQUE (org_id, connector_id),
    UNIQUE (org_id, id)
);
ALTER TABLE aegis.integrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE aegis.integrations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON aegis.integrations
    USING (org_id = aegis.current_org()) WITH CHECK (org_id = aegis.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON aegis.integrations TO aegis_app;
