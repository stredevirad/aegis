"""Versioned input contract of the RCA core.

A ``TelemetrySnapshot`` is everything the core is allowed to know about an incident:
explicitly windowed telemetry with per-item provenance and a record of which collections
succeeded. The core never fetches data, reads a clock, or touches a database (ADR-008), so
the same snapshot always produces the same analysis.

Every string that originates in telemetry is untrusted. Field caps here bound memory and
make oversized payloads a validation error instead of a resource-exhaustion vector
(audit finding S-M2); collectors truncate before constructing these models.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SNAPSHOT_SCHEMA_VERSION = "1"

MAX_TEXT = 2048
MAX_NAME = 200
MAX_ATTRIBUTES = 32
MAX_ATTR_VALUE = 256
MAX_SERIES = 5_000
MAX_POINTS_PER_SERIES = 5_000
MAX_LOGS = 50_000
MAX_SPANS = 100_000
MAX_CHANGES = 5_000
MAX_EDGES = 5_000


def _check_attributes(value: dict[str, str]) -> dict[str, str]:
    if len(value) > MAX_ATTRIBUTES:
        raise ValueError(f"at most {MAX_ATTRIBUTES} attributes allowed")
    for k, v in value.items():
        if len(k) > MAX_NAME or len(v) > MAX_ATTR_VALUE:
            raise ValueError("attribute key or value too long")
    return value


Name = Annotated[str, Field(min_length=1, max_length=MAX_NAME)]
Text = Annotated[str, Field(max_length=MAX_TEXT)]
Attributes = Annotated[dict[str, str], AfterValidator(_check_attributes)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Modality(StrEnum):
    METRICS = "metrics"
    LOGS = "logs"
    TRACES = "traces"
    CHANGES = "changes"
    TOPOLOGY = "topology"


class CollectionStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"


class TimeWindow(_Frozen):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.end <= self.start:
            raise ValueError("window end must be after start")
        return self

    def contains(self, ts: datetime) -> bool:
        return self.start <= ts < self.end

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


class Provenance(_Frozen):
    """Where an item came from. ``query`` is a canonical, credential-free description."""

    provider: Name
    connector_id: Name | None = None
    query: Text | None = None
    collected_at: AwareDatetime


class CollectionOutcome(_Frozen):
    modality: Modality
    provider: Name
    status: CollectionStatus
    detail: Text = ""
    items: int = Field(default=0, ge=0)
    truncated: bool = False


class SignalKind(StrEnum):
    REQUEST_RATE = "request_rate"  # requests per second
    ERROR_RATIO = "error_ratio"  # failed / total, 0..1
    LATENCY_P95_MS = "latency_p95_ms"
    SATURATION = "saturation"  # utilisation of a resource, 0..1


class MetricPoint(_Frozen):
    ts: AwareDatetime
    value: float = Field(allow_inf_nan=False)
    # Number of underlying events the value summarises (requests for a ratio, samples for a
    # percentile). Lets the core enforce minimum-count rules on low-traffic services.
    count: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class MetricSeries(_Frozen):
    service: Name
    signal: SignalKind
    points: Annotated[list[MetricPoint], Field(max_length=MAX_POINTS_PER_SERIES)]
    labels: Attributes = Field(default_factory=dict)
    provenance: Provenance


class Severity(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"
    UNKNOWN = "UNKNOWN"


class LogRecord(_Frozen):
    ts: AwareDatetime
    service: Name
    severity: Severity = Severity.UNKNOWN
    body: Text = ""
    instance: Name | None = None
    trace_id: Annotated[str, Field(max_length=64)] | None = None
    span_id: Annotated[str, Field(max_length=32)] | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    attributes: Attributes = Field(default_factory=dict)
    provenance: Provenance


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    UNSET = "unset"  # instrumentation reported no status: treated as not-an-error
    UNKNOWN = "unknown"  # the backend/collector did not provide status at all


class SpanKind(StrEnum):
    SERVER = "server"
    CLIENT = "client"
    INTERNAL = "internal"
    PRODUCER = "producer"
    CONSUMER = "consumer"
    UNKNOWN = "unknown"


class Span(_Frozen):
    trace_id: Annotated[str, Field(min_length=1, max_length=64)]
    span_id: Annotated[str, Field(min_length=1, max_length=32)]
    parent_span_id: Annotated[str, Field(max_length=32)] | None = None
    service: Name
    operation: Name
    kind: SpanKind = SpanKind.UNKNOWN
    start: AwareDatetime
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    status: SpanStatus = SpanStatus.UNKNOWN
    peer_service: Name | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    attributes: Attributes = Field(default_factory=dict)
    provenance: Provenance

    @property
    def failed(self) -> bool:
        return self.status is SpanStatus.ERROR

    @property
    def status_known(self) -> bool:
        return self.status is not SpanStatus.UNKNOWN


class ChangeKind(StrEnum):
    DEPLOYMENT = "deployment"
    CONFIG = "config"
    FEATURE_FLAG = "feature_flag"
    ROLLOUT = "rollout"
    OTHER = "other"


class ChangeEvent(_Frozen):
    source_id: Name
    ts: AwareDatetime
    service: Name | None
    kind: ChangeKind
    summary: Text
    environment: Name | None = None
    # Scope of the change, e.g. {"flag_key": "...", "new_value": "on", "version": "1.4.2"}.
    # ``instances`` (pods/hosts) enables scope matching against failing instances.
    scope: Attributes = Field(default_factory=dict)
    instances: Annotated[list[Name], Field(max_length=200)] = Field(default_factory=list)
    provenance: Provenance


class EdgeSource(StrEnum):
    CONFIG = "config"
    KUBERNETES = "kubernetes"


class TopologyEdge(_Frozen):
    source: Name
    target: Name
    origin: EdgeSource = EdgeSource.CONFIG


class TelemetrySnapshot(_Frozen):
    schema_version: str = SNAPSHOT_SCHEMA_VERSION
    captured_at: AwareDatetime
    collected_window: TimeWindow
    environment: Name | None = None
    synthetic: bool = False
    description: Text = ""
    metrics: Annotated[list[MetricSeries], Field(max_length=MAX_SERIES)] = Field(
        default_factory=list
    )
    logs: Annotated[list[LogRecord], Field(max_length=MAX_LOGS)] = Field(default_factory=list)
    spans: Annotated[list[Span], Field(max_length=MAX_SPANS)] = Field(default_factory=list)
    changes: Annotated[list[ChangeEvent], Field(max_length=MAX_CHANGES)] = Field(
        default_factory=list
    )
    edges: Annotated[list[TopologyEdge], Field(max_length=MAX_EDGES)] = Field(default_factory=list)
    collection: list[CollectionOutcome] = Field(default_factory=list)

    @model_validator(mode="after")
    def _version(self) -> TelemetrySnapshot:
        if self.schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(f"unsupported snapshot schema version {self.schema_version!r}")
        return self

    def outcome(self, modality: Modality) -> CollectionStatus:
        """Best status reported for a modality; NOT_CONFIGURED if nothing was attempted."""
        order = [
            CollectionStatus.OK,
            CollectionStatus.PARTIAL,
            CollectionStatus.FAILED,
            CollectionStatus.NOT_CONFIGURED,
        ]
        found = [o.status for o in self.collection if o.modality is modality]
        if not found:
            return CollectionStatus.NOT_CONFIGURED
        return min(found, key=order.index)


class Tolerances(_Frozen):
    """Thresholds of the deterministic analysis. Defaults are documented in docs/rca.md."""

    bucket_seconds: int = Field(default=30, ge=5, le=600)
    # Onset ordering closer than this is a tie (scrape interval + clock skew).
    precedence_tolerance_seconds: int = Field(default=60, ge=0, le=3600)
    change_lookback_minutes: int = Field(default=30, ge=1, le=1440)
    min_requests_for_ratio: int = Field(default=20, ge=1)
    error_ratio_floor: float = Field(default=0.02, gt=0, le=1)
    error_ratio_abs_increase: float = Field(default=0.01, gt=0, le=1)
    absolute_error_ratio_without_baseline: float = Field(default=0.05, gt=0, le=1)
    latency_ratio: float = Field(default=2.0, gt=1)
    latency_floor_ms: float = Field(default=50.0, ge=0)
    rate_change_ratio: float = Field(default=2.0, gt=1)
    min_rate_rps: float = Field(default=0.2, ge=0)
    saturation_high: float = Field(default=0.9, gt=0, le=1)
    robust_z: float = Field(default=4.0, gt=0)
    min_baseline_points: int = Field(default=4, ge=1)
    min_signature_count: int = Field(default=3, ge=1)
    min_signature_sources: int = Field(default=2, ge=1)
    edge_change_ratio: float = Field(default=2.0, gt=1)
    min_edge_calls: int = Field(default=5, ge=1)
    propagation_pass_share: float = Field(default=0.6, gt=0, le=1)
    propagation_fail_share: float = Field(default=0.2, ge=0, lt=1)
    error_origin_lead_share: float = Field(default=0.5, gt=0, le=1)
    max_evidence_items: int = Field(default=400, ge=10, le=5000)


class AnalysisRequest(_Frozen):
    incident: TimeWindow
    # Baseline windows in order of preference (e.g. the hour before the incident, the same
    # time on the previous day). The first valid one per service is used.
    baselines: Annotated[list[TimeWindow], Field(min_length=1, max_length=4)]
    focus_services: Annotated[list[Name], Field(max_length=20)] = Field(default_factory=list)
    declared_incident: bool = True
    tolerances: Tolerances = Field(default_factory=Tolerances)

    @model_validator(mode="after")
    def _baselines_precede_incident(self) -> AnalysisRequest:
        for b in self.baselines:
            if b.end > self.incident.start:
                raise ValueError("baseline windows must end before the incident window starts")
        return self
