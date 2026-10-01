"""Output contract of the RCA core: the evidence graph, candidates, checks and verdict.

Evidence IDs are derived from evidence content, so they are stable across replays of the
same snapshot. They are unique only within one analysis; persistence keys evidence by
(org_id, incident_id, evidence_id), so the same trace seen by two incidents is two rows and
can never be moved between incidents (audit finding DATA-01 / S-C1).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from aegis.core.model import AnalysisRequest, Modality

ANALYSIS_SCHEMA_VERSION = "1"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceKind(StrEnum):
    SIGNAL_ANOMALY = "signal_anomaly"  # a service signal deviating from its baseline
    SIGNAL_NORMAL = "signal_normal"  # a signal that stayed within baseline (contradiction source)
    ERROR_ORIGIN = "error_origin"  # failing traces whose deepest failing span is in a service
    TRACE_EXEMPLAR = "trace_exemplar"  # one failing trace, for drill-down
    LOG_SIGNATURE = "log_signature"  # a counted log template
    CHANGE = "change"  # deployment / config / flag / rollout event
    EDGE_CHANGE = "edge_change"  # call pattern between two services changed
    COVERAGE_GAP = "coverage_gap"  # telemetry that could not be collected or evaluated


class Evidence(_Frozen):
    id: str
    kind: EvidenceKind
    modality: Modality | None
    service: str | None
    observed_at: AwareDatetime | None
    # Deterministic, template-generated description. Telemetry-derived values inside it
    # (service names, log templates) are data, never instructions.
    summary: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    # Provenance: provider + canonical query for every underlying item.
    sources: list[dict[str, str]] = Field(default_factory=list)


class RelationKind(StrEnum):
    CALLS = "calls"  # service -> service
    OBSERVED_IN = "observed_in"  # evidence -> service
    EXEMPLIFIES = "exemplifies"  # trace exemplar -> error origin evidence
    PRECEDES = "precedes"  # change -> anomaly
    SUPPORTS = "supports"  # evidence -> candidate
    CONTRADICTS = "contradicts"  # evidence -> candidate


class Relation(_Frozen):
    source: str
    target: str
    kind: RelationKind


class CheckState(StrEnum):
    PASS = "pass"  # noqa: S105 - check outcome, not a credential
    FAIL = "fail"
    NOT_EVALUABLE = "not_evaluable"  # data needed to run the check is missing
    NOT_APPLICABLE = "not_applicable"  # the check has no meaning here (e.g. no dependents)


class CheckName(StrEnum):
    TEMPORAL_PRECEDENCE = "temporal_precedence"
    PROPAGATION = "propagation"
    ISOLATION = "isolation"
    CHANGE_SCOPE = "change_scope"
    MODALITY_AGREEMENT = "modality_agreement"
    DOWNSTREAM_EVALUATED = "downstream_evaluated"
    BASELINE_VALID = "baseline_valid"


class Check(_Frozen):
    name: CheckName
    state: CheckState
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    # Machine-readable refinement of the state, e.g. "tie" for a temporal check whose onsets
    # fall within the tolerance band.
    qualifier: str | None = None


class HypothesisKind(StrEnum):
    INTERNAL_FAILURE = "internal_failure"
    DEPENDENCY_FAILURE = "dependency_failure"
    CHANGE_INDUCED = "change_induced"
    SATURATION = "saturation"
    TRAFFIC_SURGE = "traffic_surge"
    BEHAVIOUR_CHANGE = "behaviour_change"


class Mechanism(StrEnum):
    CHANGE_INDUCED_DEPLOYMENT = "change_induced:deployment"
    CHANGE_INDUCED_CONFIG = "change_induced:config"
    CHANGE_INDUCED_FEATURE_FLAG = "change_induced:feature_flag"
    CHANGE_INDUCED_ROLLOUT = "change_induced:rollout"
    CHANGE_INDUCED_OTHER = "change_induced:other"
    SATURATION = "saturation"
    DEPENDENCY_LATENCY = "dependency_latency"
    DEPENDENCY_FAILURE = "dependency_failure"
    ERROR_THROWING = "error_throwing"
    LATENCY_DEGRADATION = "latency_degradation"
    TRAFFIC_SURGE = "traffic_surge"
    BEHAVIOUR_CHANGE = "behaviour_change"
    UNKNOWN = "unknown"


class Candidate(_Frozen):
    service: str
    rank: int
    rule: str | None  # id of the decision-list rule that selected it as a leader, if any
    rule_sentence: str | None
    hypothesis: HypothesisKind
    inferred: bool = False  # the service has no own telemetry; inferred from callers' spans
    features: dict[str, Any]
    checks: list[Check] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)


class Classification(StrEnum):
    CONFIRMED = "CONFIRMED"
    PLAUSIBLE = "PLAUSIBLE"
    INSUFFICIENT = "INSUFFICIENT"
    NO_ANOMALY = "NO_ANOMALY"


class Support(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NONE = "none"


SUPPORT_ORDER = [Support.NONE, Support.WEAK, Support.MODERATE, Support.STRONG]
CLASSIFICATION_STRENGTH = {
    Classification.NO_ANOMALY: 0,
    Classification.INSUFFICIENT: 0,
    Classification.PLAUSIBLE: 1,
    Classification.CONFIRMED: 2,
}


class NextCheck(_Frozen):
    action: str
    reason: str
    services: list[str] = Field(default_factory=list)


class RootCause(_Frozen):
    service: str
    mechanism: Mechanism
    support: Support
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str] = Field(default_factory=list)


class Verdict(_Frozen):
    classification: Classification
    support: Support
    root_causes: list[RootCause] = Field(default_factory=list)
    display_label: str
    summary: str
    reasons: list[str]
    next_checks: list[NextCheck] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    blast_radius: list[str] = Field(default_factory=list)
    other_anomalies: list[str] = Field(default_factory=list)


class Analysis(_Frozen):
    schema_version: str = ANALYSIS_SCHEMA_VERSION
    request: AnalysisRequest
    snapshot_digest: str
    evidence: list[Evidence]
    relations: list[Relation]
    candidates: list[Candidate]
    verdict: Verdict
    decision_trace: list[str]

    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.id: e for e in self.evidence}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    raise TypeError(f"not serialisable: {type(value).__name__}")


def evidence_id(kind: EvidenceKind, identity: dict[str, Any]) -> str:
    digest = hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()[:16]
    return f"ev-{kind.value.replace('_', '-')}-{digest}"


def digest_of(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
