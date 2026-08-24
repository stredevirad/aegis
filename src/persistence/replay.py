"""Evidence Replay & Reproducibility Infrastructure.

Phase 3.6: Provides compact snapshotting and offline replay of collected Evidence
so prompt experiments, summarizer evaluations, and RCA evaluator benchmarking
can be reproduced deterministically without re-injecting live faults.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime

from src.domain import (
    Evidence,
    EvidenceType,
    HealthStatus,
    Incident,
    LogsProvider,
    MetricsProvider,
    ProviderStatus,
    ServiceHealth,
    TopologyProvider,
    TracesProvider,
)


@dataclass
class EvidenceSnapshot:
    """Compact, reproducible snapshot of investigation evidence."""
    snapshot_id: str
    scenario_id: str
    captured_at: str
    target_service: str
    affected_service: str
    evidence_items: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent, default=str)

    @classmethod
    def from_json(cls, json_str: str) -> "EvidenceSnapshot":
        data = json.loads(json_str)
        return cls(
            snapshot_id=data["snapshot_id"],
            scenario_id=data.get("scenario_id", "unknown"),
            captured_at=data.get("captured_at", datetime.now(timezone.utc).isoformat()),
            target_service=data.get("target_service", "unknown"),
            affected_service=data.get("affected_service", "unknown"),
            evidence_items=data.get("evidence_items", []),
            metadata=data.get("metadata", {}),
        )


def create_snapshot_from_incident(incident: Incident, metadata: dict[str, Any] | None = None) -> EvidenceSnapshot:
    """Creates a compact EvidenceSnapshot from an active Incident."""
    compact_evidence = []
    for e in incident.evidence:
        compact_evidence.append({
            "id": e.id,
            "type": e.type.value,
            "source_provider": e.source_provider,
            "service_resource": e.service_resource,
            "summary": e.summary,
            "observed_at": e.observed_at.isoformat() if e.observed_at else None,
            "collected_at": e.collected_at.isoformat() if e.collected_at else None,
            "data": e.data if isinstance(e.data, dict) else {},
        })

    return EvidenceSnapshot(
        snapshot_id=f"snap_{incident.id}",
        scenario_id=incident.scenario_id or "unknown",
        captured_at=datetime.now(timezone.utc).isoformat(),
        target_service=incident.affected_service or "unknown",
        affected_service=incident.affected_service or "unknown",
        evidence_items=compact_evidence,
        metadata=metadata or {},
    )


def save_snapshot(snapshot: EvidenceSnapshot, filepath: str | Path) -> None:
    """Save evidence snapshot to a compact JSON file."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(snapshot.to_json())


def load_snapshot(filepath: str | Path) -> EvidenceSnapshot:
    """Load evidence snapshot from a JSON file."""
    path = Path(filepath)
    with open(path, "r", encoding="utf-8") as f:
        return EvidenceSnapshot.from_json(f.read())


def reconstruct_evidence(snapshot: EvidenceSnapshot) -> list[Evidence]:
    """Reconstruct domain Evidence objects from a snapshot."""
    evidence_list = []
    now = datetime.now(timezone.utc)
    
    for item in snapshot.evidence_items:
        etype_str = item.get("type", "METRIC").upper()
        try:
            etype = EvidenceType(etype_str)
        except ValueError:
            etype = EvidenceType.METRIC

        obs_str = item.get("observed_at")
        coll_str = item.get("collected_at")
        
        try:
            observed_at = datetime.fromisoformat(obs_str) if obs_str else now
        except ValueError:
            observed_at = now
            
        try:
            collected_at = datetime.fromisoformat(coll_str) if coll_str else now
        except ValueError:
            collected_at = now

        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if collected_at.tzinfo is None:
            collected_at = collected_at.replace(tzinfo=timezone.utc)

        evidence_list.append(
            Evidence(
                id=item["id"],
                type=etype,
                source_provider=item.get("source_provider", "replay"),
                service_resource=item.get("service_resource"),
                summary=item.get("summary", ""),
                data=item.get("data", {}),
                observed_at=observed_at,
                collected_at=collected_at,
            )
        )
    return evidence_list


class ReplayProvider(MetricsProvider, LogsProvider, TracesProvider):
    """Unified telemetry provider that replays Evidence from a snapshot.
    
    Supports offline testing of investigation workflows, LLM reasoning,
    summarization, and evaluator algorithms without live backends.
    """
    def __init__(self, snapshot: EvidenceSnapshot) -> None:
        self.snapshot = snapshot
        self.evidence = reconstruct_evidence(snapshot)

    def get_status(self) -> ProviderStatus:
        return ProviderStatus(
            provider="replay",
            health=HealthStatus.HEALTHY,
            capabilities=["query_metrics", "search_logs", "search_traces", "get_trace"],
        )

    def get_service_health(self, service_name: str) -> ServiceHealth:
        return ServiceHealth(
            service_id=service_name,
            status=HealthStatus.DEGRADED if any(e.service_resource == service_name for e in self.evidence) else HealthStatus.HEALTHY,
            summary=f"Replay health for {service_name}",
            observed_at=datetime.now(timezone.utc),
        )

    def query_metrics(
        self, query: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        return [
            e for e in self.evidence
            if e.type == EvidenceType.METRIC and (
                not query or (e.service_resource and query.lower() in e.service_resource.lower()) or (e.summary and query.lower() in e.summary.lower())
            )
        ]

    def search_logs(
        self, query: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        return [
            e for e in self.evidence
            if e.type == EvidenceType.LOG and (
                not query or (e.service_resource and query.lower() in e.service_resource.lower()) or (e.summary and query.lower() in e.summary.lower())
            )
        ]

    def search_traces(
        self, service_name: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        return self.get_related_traces(service_name, start_time, end_time)

    def get_related_traces(
        self, service_name: str, start_time: datetime, end_time: datetime
    ) -> list[Evidence]:
        return [
            e for e in self.evidence
            if e.type == EvidenceType.TRACE and (
                not service_name or (e.service_resource and service_name.lower() in e.service_resource.lower())
            )
        ]

    def get_trace(self, trace_id: str) -> Evidence | None:
        for e in self.evidence:
            if e.type == EvidenceType.TRACE and (e.id == trace_id or trace_id in str(e.data.get("trace_id", ""))):
                return e
        return None
