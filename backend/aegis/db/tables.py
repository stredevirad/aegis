"""SQLAlchemy Core descriptions of the tables the application reads and writes.

The schema itself is owned by the SQL migrations (``aegis/db/migrations``); these objects only
let repositories build parameterised statements without string concatenation. A database
test (``tests/db/test_schema_drift.py``) asserts that every column declared here exists in the
migrated schema with a compatible type, so the two cannot silently diverge.

Only columns the application uses are declared; server defaults stay in SQL.
"""

from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    FetchedValue,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData(schema="aegis")


def _ts(name: str, **kw: object) -> Column[object]:
    return Column(name, DateTime(timezone=True), **kw)  # type: ignore[arg-type]


organizations = Table(
    "organizations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("slug", Text),
    Column("name", Text),
    Column("settings", JSONB),
    _ts("created_at"),
)

users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("issuer", Text),
    Column("subject", Text),
    Column("email", Text),
    Column("email_verified", Boolean),
    Column("display_name", Text),
    Column("disabled", Boolean),
    _ts("created_at"),
)

memberships = Table(
    "memberships",
    metadata,
    Column("org_id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), primary_key=True),
    Column("role", Text),
    _ts("created_at"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("token_hash", LargeBinary),
    Column("org_id", UUID(as_uuid=True)),
    Column("user_id", UUID(as_uuid=True)),
    _ts("created_at"),
    _ts("last_seen_at"),
    _ts("idle_expires_at"),
    _ts("expires_at"),
    _ts("revoked_at"),
    Column("user_agent", Text),
)

invitations = Table(
    "invitations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("org_id", UUID(as_uuid=True)),
    Column("email", Text),
    Column("role", Text),
    Column("invited_by", UUID(as_uuid=True)),
    _ts("created_at"),
    _ts("expires_at"),
    _ts("accepted_at"),
    Column("accepted_by", UUID(as_uuid=True)),
    _ts("revoked_at"),
)

api_keys = Table(
    "api_keys",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("org_id", UUID(as_uuid=True)),
    Column("name", Text),
    Column("prefix", Text),
    Column("secret_hash", LargeBinary),
    Column("scopes", ARRAY(Text)),
    Column("created_by", UUID(as_uuid=True)),
    _ts("created_at"),
    _ts("expires_at"),
    _ts("last_used_at"),
    _ts("revoked_at"),
    Column("revoked_reason", Text),
    Column("rotated_from", UUID(as_uuid=True)),
)

incidents = Table(
    "incidents",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("org_id", UUID(as_uuid=True)),
    Column("title", Text),
    Column("description", Text),
    Column("severity", Text),
    Column("status", Text),
    Column("service", Text),
    Column("environment", Text),
    Column("source", Text),
    Column("group_key", Text),
    Column("labels", JSONB),
    Column("annotations", JSONB),
    _ts("started_at"),
    _ts("resolved_at"),
    Column("created_by", Text),
    _ts("created_at"),
    _ts("updated_at"),
    _ts("last_alert_at"),
    Column("alert_count", Integer),
    _ts("auto_investigated_at"),
)

alerts = Table(
    "alerts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("org_id", UUID(as_uuid=True)),
    Column("incident_id", UUID(as_uuid=True)),
    Column("source", Text),
    Column("fingerprint", Text),
    Column("status", Text),
    Column("name", Text),
    Column("severity", Text),
    Column("service", Text),
    Column("environment", Text),
    Column("labels", JSONB),
    Column("annotations", JSONB),
    Column("generator_url", Text),
    _ts("starts_at"),
    _ts("ends_at"),
    _ts("first_received_at"),
    _ts("last_received_at"),
    Column("receive_count", Integer),
    Column("received_via", UUID(as_uuid=True)),
)

change_events = Table(
    "change_events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("org_id", UUID(as_uuid=True)),
    Column("source", Text),
    Column("external_id", Text),
    Column("kind", Text),
    Column("service", Text),
    Column("environment", Text),
    Column("version", Text),
    Column("summary", Text),
    Column("actor", Text),
    _ts("ts"),
    _ts("received_at"),
    Column("scope", JSONB),
    Column("metadata", JSONB),
    Column("received_via", UUID(as_uuid=True)),
)

investigations = Table(
    "investigations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("org_id", UUID(as_uuid=True)),
    Column("incident_id", UUID(as_uuid=True)),
    Column("status", Text),
    Column("trigger", Text),
    Column("requested_by", Text),
    Column("idempotency_key", Text),
    _ts("window_start"),
    _ts("window_end"),
    _ts("baseline_start"),
    _ts("baseline_end"),
    Column("source", JSONB),
    Column("classification", Text),
    Column("support", Text),
    Column("display_label", Text),
    Column("summary", Text),
    Column("verdict", JSONB),
    Column("error", Text),
    Column("cancel_requested", Boolean),
    _ts("created_at"),
    _ts("started_at"),
    _ts("finished_at"),
    Column("event_seq", Integer),
)

investigation_jobs = Table(
    "investigation_jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("org_id", UUID(as_uuid=True)),
    Column("investigation_id", UUID(as_uuid=True)),
    Column("status", Text),
    Column("attempts", Integer),
    Column("max_attempts", Integer),
    _ts("available_at"),
    Column("lease_owner", Text),
    _ts("lease_expires_at"),
    Column("last_error", Text),
    _ts("created_at"),
    _ts("updated_at"),
    Column("priority", SmallInteger),
)

investigation_events = Table(
    "investigation_events",
    metadata,
    Column("id", BigInteger, primary_key=True, server_default=FetchedValue()),
    Column("org_id", UUID(as_uuid=True)),
    Column("investigation_id", UUID(as_uuid=True)),
    Column("seq", Integer),
    _ts("ts"),
    Column("kind", Text),
    Column("state", Text),
    Column("actor", Text),
    Column("message", Text),
    Column("data", JSONB),
)

snapshots = Table(
    "snapshots",
    metadata,
    Column("org_id", UUID(as_uuid=True), primary_key=True),
    Column("investigation_id", UUID(as_uuid=True), primary_key=True),
    Column("schema_version", Text),
    Column("digest", Text),
    Column("synthetic", Boolean),
    Column("payload", JSONB),
    Column("redaction", JSONB),
    _ts("created_at"),
    _ts("expires_at"),
)

evidence = Table(
    "evidence",
    metadata,
    Column("org_id", UUID(as_uuid=True), primary_key=True),
    Column("investigation_id", UUID(as_uuid=True), primary_key=True),
    Column("evidence_id", Text, primary_key=True),
    Column("kind", Text),
    Column("modality", Text),
    Column("service", Text),
    _ts("observed_at"),
    Column("summary", Text),
    Column("attributes", JSONB),
    Column("sources", JSONB),
)

evidence_relations = Table(
    "evidence_relations",
    metadata,
    Column("org_id", UUID(as_uuid=True), primary_key=True),
    Column("investigation_id", UUID(as_uuid=True), primary_key=True),
    Column("source", Text, primary_key=True),
    Column("target", Text, primary_key=True),
    Column("kind", Text, primary_key=True),
)

candidates = Table(
    "candidates",
    metadata,
    Column("org_id", UUID(as_uuid=True), primary_key=True),
    Column("investigation_id", UUID(as_uuid=True), primary_key=True),
    Column("service", Text, primary_key=True),
    Column("rank", Integer),
    Column("rule", Text),
    Column("rule_sentence", Text),
    Column("hypothesis", Text),
    Column("inferred", Boolean),
    Column("features", JSONB),
    Column("checks", JSONB),
    Column("supporting", ARRAY(Text)),
    Column("contradicting", ARRAY(Text)),
)

root_causes = Table(
    "root_causes",
    metadata,
    Column("org_id", UUID(as_uuid=True), primary_key=True),
    Column("investigation_id", UUID(as_uuid=True), primary_key=True),
    Column("service", Text, primary_key=True),
    Column("mechanism", Text),
    Column("support", Text),
    Column("supporting", ARRAY(Text)),
    Column("contradicting", ARRAY(Text)),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", BigInteger, primary_key=True, server_default=FetchedValue()),
    Column("org_id", UUID(as_uuid=True)),
    Column("seq", BigInteger),
    _ts("ts"),
    Column("actor_type", Text),
    Column("actor_id", Text),
    Column("action", Text),
    Column("target_type", Text),
    Column("target_id", Text),
    Column("details", JSONB),
    Column("request_id", Text),
    Column("prev_hash", LargeBinary),
    Column("hash", LargeBinary),
)

integrations = Table(
    "integrations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=FetchedValue()),
    Column("org_id", UUID(as_uuid=True)),
    Column("connector_id", Text),
    Column("kind", Text),
    Column("name", Text),
    Column("enabled", Boolean),
    Column("credential_type", Text),
    Column("credential_kid", Text),
    Column("credential_blob", LargeBinary),
    Column("created_by", UUID(as_uuid=True)),
    _ts("created_at"),
    _ts("updated_at"),
    _ts("last_test_at"),
    Column("last_test_status", Text),
    Column("last_test_detail", Text),
)
