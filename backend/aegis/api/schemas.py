"""Explicit request and response schemas of ``/api/v1``.

Response models are built from database rows field by field; internal structures (rows,
SQLAlchemy objects, core models) are never returned directly. Strings that originate in
telemetry or alerts were sanitised on ingestion and are still untrusted: clients must render
them as text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from aegis.investigations.sources import SourceSpec

Severity = Literal["critical", "high", "medium", "low", "info"]
IncidentStatus = Literal["open", "investigating", "resolved", "closed"]


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


# --- identity -------------------------------------------------------------------------------


class OrgRef(_Out):
    id: UUID
    slug: str
    name: str
    role: str


class MeOut(_Out):
    kind: Literal["user", "api_key"]
    org: OrgRef | None
    user_id: UUID | None
    email: str | None
    display_name: str | None
    role: str | None
    permissions: list[str]
    memberships: list[OrgRef]
    api_key_prefix: str | None


# --- incidents ------------------------------------------------------------------------------


class IncidentOut(_Out):
    id: UUID
    title: str
    description: str
    severity: Severity
    status: IncidentStatus
    service: str | None
    environment: str | None
    source: str
    labels: dict[str, Any]
    annotations: dict[str, Any]
    started_at: datetime
    resolved_at: datetime | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    last_alert_at: datetime | None
    alert_count: int
    grouping_open: bool
    auto_investigated_at: datetime | None


class IncidentDetailOut(IncidentOut):
    investigations: int
    latest_investigation: InvestigationOut | None


class IncidentCreate(_In):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    severity: Severity
    description: Annotated[str, Field(max_length=4000)] = ""
    service: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    environment: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    started_at: AwareDatetime | None = None


class IncidentUpdate(_In):
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    severity: Severity | None = None
    status: IncidentStatus | None = None


class AlertOut(_Out):
    id: UUID
    incident_id: UUID
    source: str
    fingerprint: str
    status: str
    name: str
    severity: Severity
    service: str | None
    environment: str | None
    labels: dict[str, Any]
    annotations: dict[str, Any]
    generator_url: str | None
    starts_at: datetime
    ends_at: datetime | None
    first_received_at: datetime
    last_received_at: datetime
    receive_count: int


# --- investigations -------------------------------------------------------------------------


class WindowOut(_Out):
    start: datetime
    end: datetime


class WindowIn(_In):
    start: AwareDatetime
    end: AwareDatetime


class RootCauseOut(_Out):
    service: str
    mechanism: str
    support: str
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]


class NextCheckOut(_Out):
    action: str
    reason: str
    services: list[str]


class InvestigationOut(_Out):
    id: UUID
    incident_id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    queue_state: str | None
    attempts: int | None
    trigger: str
    requested_by: str | None
    source: dict[str, Any]
    synthetic: bool
    incident_window: WindowOut
    baseline_window: WindowOut
    classification: str | None
    support: str | None
    display_label: str | None
    summary: str | None
    root_causes: list[RootCauseOut]
    reasons: list[str]
    next_checks: list[NextCheckOut]
    coverage_gaps: list[str]
    blast_radius: list[str]
    other_anomalies: list[str]
    error: str | None
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class InvestigationStart(_In):
    source: SourceSpec | None = None
    # Explicit incident window for live sources (defaults to the incident's start).
    window: WindowIn | None = None


class CancelOut(_Out):
    investigation_id: UUID
    result: Literal["cancelled", "cancel_requested"]


class EventOut(_Out):
    seq: int
    ts: datetime
    kind: str
    state: str | None
    actor: str | None
    message: str
    data: dict[str, Any]


class EvidenceOut(_Out):
    evidence_id: str
    kind: str
    modality: str | None
    service: str | None
    observed_at: datetime | None
    summary: str
    attributes: dict[str, Any]
    sources: list[dict[str, Any]]


class CheckOut(_Out):
    name: str
    state: str
    reason: str
    evidence_ids: list[str]
    qualifier: str | None = None


class CandidateOut(_Out):
    service: str
    rank: int
    rule: str | None
    rule_sentence: str | None
    hypothesis: str
    inferred: bool
    features: dict[str, Any]
    checks: list[CheckOut]
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]


class RelationOut(_Out):
    source: str
    target: str
    kind: str


class GraphOut(_Out):
    investigation_id: UUID
    candidates: list[CandidateOut]
    root_causes: list[RootCauseOut]
    relations: list[RelationOut]
    decision_trace: list[str]


class CollectionOut(_Out):
    modality: str
    provider: str
    status: str
    detail: str
    items: int
    truncated: bool


class SnapshotSummaryOut(_Out):
    investigation_id: UUID
    digest: str
    synthetic: bool
    schema_version: str
    captured_at: datetime | None
    collected_window: WindowOut | None
    counts: dict[str, int]
    collection: list[CollectionOut]
    redaction: dict[str, Any]


# --- ingestion ------------------------------------------------------------------------------


class IngestOut(_Out):
    received: int
    new_occurrences: int
    duplicates: int
    ignored_resolved: int
    clock_skew_clamped: int
    incidents_created: list[UUID]
    incidents_updated: list[UUID]
    incidents_resolved: list[UUID]
    investigations: dict[str, str]


class ChangeEventOut(_Out):
    id: UUID
    source: str
    external_id: str | None
    kind: str
    service: str | None
    environment: str | None
    version: str | None
    summary: str
    actor: str | None
    ts: datetime
    received_at: datetime
    scope: dict[str, Any]
    metadata: dict[str, Any]


class ChangeIngestOut(_Out):
    id: UUID
    created: bool


# --- administration -------------------------------------------------------------------------


class AuditEventOut(_Out):
    seq: int
    ts: datetime
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    request_id: str | None
    hash: str


class AuditVerifyOut(_Out):
    intact: bool
    first_broken_seq: int | None
    events: int
    head_hash: str | None
    note: str


class ApiKeyOut(_Out):
    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    created_by: UUID | None
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    revoked_reason: str | None
    rotated_from: UUID | None
    active: bool


class ApiKeyCreate(_In):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    scopes: Annotated[
        list[Literal["ingest:alerts", "ingest:changes", "read"]], Field(min_length=1, max_length=3)
    ]
    expires_in_days: Annotated[int, Field(ge=1, le=365)] = 90


class ApiKeyCreated(_Out):
    key: ApiKeyOut
    secret: str  # shown once


class MemberOut(_Out):
    user_id: UUID
    email: str | None
    display_name: str | None
    role: str
    created_at: datetime


class MemberUpdate(_In):
    role: Literal["admin", "investigator", "reviewer", "viewer"]


class InvitationCreate(_In):
    email: Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")]
    role: Literal["admin", "investigator", "reviewer", "viewer"]
    expires_in_days: Annotated[int, Field(ge=1, le=30)] = 7


class InvitationOut(_Out):
    id: UUID
    email: str
    role: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


class OrganizationOut(_Out):
    id: UUID
    slug: str
    name: str
    settings: dict[str, Any]


class SourcesOut(_Out):
    live: bool
    scenarios: list[str]
    note: str


IncidentDetailOut.model_rebuild()
