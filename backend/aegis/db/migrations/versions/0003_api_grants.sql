-- Migration 0003: grants needed by the API process.
-- Readiness probe: the application checks that the schema is at the version it expects.
GRANT SELECT ON aegis.alembic_version TO aegis_app;
