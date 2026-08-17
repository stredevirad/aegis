from enum import Enum
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field

# --- Shared Enums ---


class HealthStatus(str, Enum):
    """Unified health status for both services and providers."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class InvestigationStatus(str, Enum):
    """State machine states for an incident investigation."""

    INCIDENT_CREATED = "INCIDENT_CREATED"
    COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
    FORMING_HYPOTHESES = "FORMING_HYPOTHESES"
    VERIFYING_HYPOTHESES = "VERIFYING_HYPOTHESES"
    CONCLUDING = "CONCLUDING"
    RECOMMENDATION = "RECOMMENDATION"
    RECORDED = "RECORDED"
    FAILED = "FAILED"


# --- Service Health ---


class ServiceHealth(BaseModel):
    service_id: str
    status: HealthStatus = HealthStatus.UNKNOWN
    summary: str | None = None
    observed_at: AwareDatetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Evidence ---


class EvidenceType(str, Enum):
    METRIC = "METRIC"
    LOG = "LOG"
    TRACE = "TRACE"
    DEPLOYMENT = "DEPLOYMENT"
    TOPOLOGY = "TOPOLOGY"
    UNKNOWN = "UNKNOWN"


class Evidence(BaseModel):
    id: str
    type: EvidenceType
    source_provider: str
    service_resource: str | None = None
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    observed_at: AwareDatetime
    collected_at: AwareDatetime


# --- Hypothesis ---


class HypothesisStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"


class Hypothesis(BaseModel):
    id: str
    description: str
    status: HypothesisStatus = HypothesisStatus.UNKNOWN
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)


# --- Incident ---


class Incident(BaseModel):
    id: str
    title: str
    description: str
    start_time: AwareDatetime
    end_time: AwareDatetime | None = None
    status: InvestigationStatus = InvestigationStatus.INCIDENT_CREATED
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    environment_profile: str | None = None
    available_capabilities: list[str] = Field(default_factory=list)
    scenario_id: str | None = None
    ground_truth_reference: str | None = None


# --- Provider Status ---


class ProviderStatus(BaseModel):
    provider: str
    capabilities: list[str] = Field(default_factory=list)
    health: HealthStatus = HealthStatus.UNKNOWN
    limitations: list[str] = Field(default_factory=list)


# --- Remediation ---


class RemediationRecommendation(BaseModel):
    action: str
    reason: str
    expected_impact: str
    risk_uncertainty: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
