"""Turns stage outputs into evidence items and per-service facts.

Evidence summaries are generated from templates; telemetry-derived strings inside them
(service names, log templates) are bounded data. Nothing here reads a clock or performs I/O.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from aegis.core.logs import ErrorLogRate, SignatureFinding
from aegis.core.model import (
    AnalysisRequest,
    ChangeEvent,
    CollectionStatus,
    Modality,
    SignalKind,
    TelemetrySnapshot,
)
from aegis.core.result import Evidence, EvidenceKind, Relation, RelationKind, evidence_id
from aegis.core.sanitize import single_line
from aegis.core.signals import SignalAssessment
from aegis.core.stats import quantile
from aegis.core.topology import EdgeChange, Graph
from aegis.core.traces import EdgeCounts, OriginStats, TraceGroup, exemplar_traces

MAX_SERVICE_NAME_IN_SUMMARY = 80


def _svc(name: str) -> str:
    return name[:MAX_SERVICE_NAME_IN_SUMMARY]


def _fmt(value: float | None, unit: str = "") -> str:
    if value is None:
        return "n/a"
    if unit == "%":
        return f"{value * 100:.1f}%"
    return f"{value:.1f}{unit}"


@dataclass
class Anomaly:
    evidence_id: str
    modality: Modality
    family: str  # "error" | "latency" | "saturation" | "rate" | "behaviour"
    onset: datetime | None
    baseline_valid: bool
    direction: str | None = None


@dataclass
class ServiceFacts:
    service: str
    anomalies: list[Anomaly] = field(default_factory=list)
    normals: list[tuple[str, str, Modality]] = field(default_factory=list)  # (id, family, modality)
    origin_evidence: str | None = None
    origin_share: float | None = None
    origin_is_anomalous: bool = False
    origin_onset: datetime | None = None
    inferred: bool = False  # no own telemetry; known only as a peer of client spans
    signature_evidence: list[str] = field(default_factory=list)
    multi_source_error_signatures: int = 0
    changes: list[tuple[str, ChangeEvent]] = field(default_factory=list)
    edge_change_evidence: list[str] = field(default_factory=list)
    evaluable: bool = False  # some telemetry with known health exists for this service
    instances: set[str] = field(default_factory=set)

    @property
    def anomalous(self) -> bool:
        return bool(self.anomalies) or self.origin_is_anomalous

    @property
    def onset(self) -> datetime | None:
        times = [a.onset for a in self.anomalies if a.onset is not None]
        if self.origin_is_anomalous and self.origin_onset is not None:
            times.append(self.origin_onset)
        return min(times) if times else None

    def modalities(self) -> set[Modality]:
        mods = {a.modality for a in self.anomalies if a.family != "behaviour"}
        if self.origin_is_anomalous:
            mods.add(Modality.TRACES)
        if self.multi_source_error_signatures:
            mods.add(Modality.LOGS)
        return mods

    def families(self) -> set[str]:
        fams = {a.family for a in self.anomalies}
        if self.origin_is_anomalous:
            fams.add("error")
        return fams


@dataclass
class EvidenceBuilder:
    items: dict[str, Evidence] = field(default_factory=dict)
    relations: set[tuple[str, str, RelationKind]] = field(default_factory=set)

    def add(
        self,
        kind: EvidenceKind,
        identity: dict[str, Any],
        *,
        modality: Modality | None,
        service: str | None,
        observed_at: datetime | None,
        summary: str,
        attributes: dict[str, Any],
        sources: list[dict[str, str]] | None = None,
    ) -> str:
        eid = evidence_id(kind, identity)
        if eid not in self.items:
            self.items[eid] = Evidence(
                id=eid,
                kind=kind,
                modality=modality,
                service=service,
                observed_at=observed_at,
                summary=summary,
                attributes=attributes,
                sources=sources or [],
            )
            if service:
                self.relate(eid, f"svc:{service}", RelationKind.OBSERVED_IN)
        return eid

    def relate(self, source: str, target: str, kind: RelationKind) -> None:
        self.relations.add((source, target, kind))

    def relation_list(self) -> list[Relation]:
        return [Relation(source=s, target=t, kind=k) for s, t, k in sorted(self.relations)]


_FAMILY = {
    SignalKind.ERROR_RATIO: "error",
    SignalKind.LATENCY_P95_MS: "latency",
    SignalKind.SATURATION: "saturation",
    SignalKind.REQUEST_RATE: "rate",
}
_UNIT = {
    SignalKind.ERROR_RATIO: "%",
    SignalKind.LATENCY_P95_MS: "ms",
    SignalKind.SATURATION: "%",
    SignalKind.REQUEST_RATE: " req/s",
}


Sources = list[dict[str, str]]


def sources_by_service(items: Iterable[Any], cap: int = 3) -> dict[str, Sources]:
    """Up to ``cap`` distinct (provider, query) pairs per service, for evidence provenance."""
    seen: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for item in items:
        seen[item.service].add((item.provenance.provider, item.provenance.query or ""))
    return {
        svc: [{"provider": p, "query": q} for p, q in sorted(pairs)[:cap]]
        for svc, pairs in seen.items()
    }


def _merged(*lists: Sources | None, cap: int = 3) -> Sources:
    pairs = sorted({(d["provider"], d.get("query", "")) for lst in lists if lst for d in lst})
    return [{"provider": p, "query": q} for p, q in pairs[:cap]]


def facts_for(services: set[str], facts: dict[str, ServiceFacts], name: str) -> ServiceFacts:
    if name not in facts:
        facts[name] = ServiceFacts(name)
        services.add(name)
    return facts[name]


def add_signal_evidence(
    eb: EvidenceBuilder,
    facts: dict[str, ServiceFacts],
    services: set[str],
    assessments: list[SignalAssessment],
) -> None:
    for a in sorted(assessments, key=lambda x: (x.service, x.signal.value, x.origin)):
        f = facts_for(services, facts, a.service)
        f.evaluable = True
        unit = _UNIT[a.signal]
        family = _FAMILY[a.signal]
        sources = [{"provider": p, "query": q} for p, q in a.sources]
        attrs: dict[str, Any] = {
            "signal": a.signal.value,
            "origin": a.origin,
            "baseline_state": a.baseline_state,
            "baseline_value": a.baseline_value,
            "incident_value": a.incident_value,
            "threshold": a.threshold,
            "direction": a.direction,
            "onset": a.onset.isoformat() if a.onset else None,
            "detail": a.detail,
        }
        identity = {"service": a.service, "signal": a.signal.value, "origin": a.origin}
        if a.anomalous:
            verb = {"up": "rose to", "down": "fell to"}.get(a.direction or "", "is")
            summary = (
                f"{_svc(a.service)} {a.signal.value} {verb} {_fmt(a.incident_value, unit)} "
                f"(baseline {_fmt(a.baseline_value, unit)}, threshold "
                f"{_fmt(a.threshold, unit)}; source: {a.origin})"
            )
            eid = eb.add(
                EvidenceKind.SIGNAL_ANOMALY,
                identity,
                modality=a.modality,
                service=a.service,
                observed_at=a.onset,
                summary=summary,
                attributes=attrs,
                sources=sources,
            )
            f.anomalies.append(
                Anomaly(eid, a.modality, family, a.onset, a.baseline_valid, a.direction)
            )
        else:
            summary = (
                f"{_svc(a.service)} {a.signal.value} within expected range at "
                f"{_fmt(a.incident_value, unit)} (baseline {_fmt(a.baseline_value, unit)}; "
                f"{a.detail})"
            )
            eid = eb.add(
                EvidenceKind.SIGNAL_NORMAL,
                identity,
                modality=a.modality,
                service=a.service,
                observed_at=None,
                summary=summary,
                attributes=attrs,
                sources=sources,
            )
            if a.baseline_valid or a.signal is SignalKind.ERROR_RATIO:
                f.normals.append((eid, family, a.modality))


def add_edge_latency_evidence(
    eb: EvidenceBuilder,
    facts: dict[str, ServiceFacts],
    services: set[str],
    request: AnalysisRequest,
    base: EdgeCounts,
    inc: EdgeCounts,
    trace_sources: dict[str, Sources] | None = None,
) -> None:
    """Latency of calls into a dependency, measured at the callers (works when the
    dependency itself is not instrumented, e.g. a database)."""
    trace_sources = trace_sources or {}
    tol = request.tolerances
    for edge in sorted(set(inc.latencies)):
        caller, callee = edge
        inc_samples = inc.latencies[edge]
        base_samples = base.latencies.get(edge, [])
        if len(inc_samples) < tol.min_edge_calls or len(base_samples) < tol.min_edge_calls:
            continue
        b95 = quantile([d for _, d in base_samples], 0.95)
        i95 = quantile([d for _, d in inc_samples], 0.95)
        threshold = max(b95 * tol.latency_ratio, b95 + tol.latency_floor_ms)
        f = facts_for(services, facts, callee)
        f.evaluable = True
        attrs = {
            "signal": "edge_latency_p95_ms",
            "caller": caller,
            "callee": callee,
            "baseline_value": b95,
            "incident_value": i95,
            "threshold": threshold,
            "calls_baseline": len(base_samples),
            "calls_incident": len(inc_samples),
        }
        identity = {"edge": [caller, callee], "signal": "edge_latency"}
        if i95 >= threshold:
            slow = sorted(t for t, d in inc_samples if d >= threshold)
            onset = slow[0] if len(slow) >= 2 else None
            attrs["onset"] = onset.isoformat() if onset else None
            eid = eb.add(
                EvidenceKind.SIGNAL_ANOMALY,
                identity,
                modality=Modality.TRACES,
                service=callee,
                observed_at=onset,
                summary=(
                    f"Calls from {_svc(caller)} to {_svc(callee)}: p95 {i95:.1f}ms "
                    f"(baseline {b95:.1f}ms, threshold {threshold:.1f}ms)"
                ),
                attributes=attrs,
                sources=_merged(trace_sources.get(caller)),
            )
            eb.relate(f"svc:{caller}", f"svc:{callee}", RelationKind.CALLS)
            f.anomalies.append(Anomaly(eid, Modality.TRACES, "latency", onset, True, "up"))


def add_origin_evidence(
    eb: EvidenceBuilder,
    facts: dict[str, ServiceFacts],
    services: set[str],
    origin: OriginStats,
    base_origin: OriginStats,
    groups: list[TraceGroup],
    request: AnalysisRequest,
    trace_sources: dict[str, Sources] | None = None,
) -> None:
    tol = request.tolerances
    trace_sources = trace_sources or {}
    all_trace_sources = _merged(*trace_sources.values())
    by_trace = {g.trace_id: g for g in groups}
    for service in sorted(origin.origin_counts):
        count = origin.origin_counts[service]
        f = facts_for(services, facts, service)
        f.evaluable = True
        if service in origin.inferred_origins:
            f.inferred = not any(g for g in groups if any(s.service == service for s in g.spans))
        inc_rate = count / max(origin.total_traces, 1)
        base_count = base_origin.origin_counts.get(service, 0)
        base_rate = base_count / base_origin.total_traces if base_origin.total_traces else None
        anomalous = count >= tol.min_signature_count and (
            base_rate is None or inc_rate >= max(3 * base_rate, base_rate + 0.01)
        )
        share = origin.share(service)
        exemplars = exemplar_traces(origin, service)
        onset = _origin_onset(origin, by_trace, service, base_rate, request) if anomalous else None
        eid = eb.add(
            EvidenceKind.ERROR_ORIGIN,
            {"service": service, "kind": "error_origin"},
            modality=Modality.TRACES,
            service=service,
            observed_at=onset,
            summary=(
                f"{count} of {origin.determined_traces} failing traces with known span status "
                f"end in {_svc(service)}"
                + (
                    " (inferred from callers' client spans)"
                    if service in origin.inferred_origins
                    else ""
                )
                + (f"; baseline rate {base_rate:.3f} per trace" if base_rate is not None else "")
            ),
            attributes={
                "origin_traces": count,
                "determined_failing_traces": origin.determined_traces,
                "undetermined_failing_traces": origin.undetermined_traces,
                "share": share,
                "baseline_origin_traces": base_count,
                "inferred": service in origin.inferred_origins,
                "exemplar_trace_ids": exemplars,
                "anomalous": anomalous,
            },
            # an inferred (uninstrumented) origin is seen through its callers' spans
            sources=trace_sources.get(service) or all_trace_sources,
        )
        f.origin_evidence = eid
        f.origin_share = share
        f.origin_is_anomalous = anomalous
        f.origin_onset = onset
        for t in exemplars:
            g = by_trace[t]
            failing = [
                {"service": s.service, "operation": s.operation, "status": s.status.value}
                for s in g.spans
                if s.failed
            ][:10]
            xid = eb.add(
                EvidenceKind.TRACE_EXEMPLAR,
                {"trace_id": t},
                modality=Modality.TRACES,
                service=service,
                observed_at=g.spans[0].start,
                summary=f"Failing trace {t[:32]}: {len(failing)} failing spans, origin {_svc(service)}",
                attributes={"trace_id": t, "failing_spans": failing, "span_count": len(g.spans)},
                sources=[
                    {"provider": p, "query": q}
                    for p, q in sorted(
                        {(s.provenance.provider, s.provenance.query or "") for s in g.spans}
                    )[:3]
                ],
            )
            eb.relate(xid, eid, RelationKind.EXEMPLIFIES)


def add_log_evidence(
    eb: EvidenceBuilder,
    facts: dict[str, ServiceFacts],
    services: set[str],
    findings: list[SignatureFinding],
    rates: list[ErrorLogRate],
    log_sources: dict[str, Sources] | None = None,
) -> None:
    log_sources = log_sources or {}
    for fd in findings:
        st = fd.stats
        if not st.error and fd.status == "present":
            continue
        f = facts_for(services, facts, st.service)
        f.evaluable = True
        f.instances |= st.incident_sources
        eid = eb.add(
            EvidenceKind.LOG_SIGNATURE,
            {"service": st.service, "template": st.template, "error": st.error},
            modality=Modality.LOGS,
            service=st.service,
            observed_at=st.first_seen,
            summary=(
                f"{'Error' if st.error else 'Warning'} log signature ({fd.status}) in "
                f"{_svc(st.service)}: {st.incident_count} occurrences from "
                f"{len(st.incident_sources)} source(s) (baseline {st.baseline_count})"
            ),
            attributes={
                "template": st.template,
                "status": fd.status,
                "error": st.error,
                "incident_count": st.incident_count,
                "baseline_count": st.baseline_count,
                "sources": len(st.incident_sources),
                "single_source": fd.single_source,
                "baseline_evaluable": fd.baseline_evaluable,
                "trace_ids": sorted(st.trace_ids)[:10],
            },
            sources=[{"provider": p, "query": q} for p, q in sorted(st.providers)[:3]],
        )
        f.signature_evidence.append(eid)
        if st.error and fd.status in ("new", "increased") and not fd.single_source:
            f.multi_source_error_signatures += 1
    for r in rates:
        f = facts_for(services, facts, r.service)
        if r.baseline_per_min is None:
            continue
        f.evaluable = True
        identity = {"service": r.service, "signal": "error_log_rate"}
        attrs = {
            "signal": "error_log_rate",
            "baseline_per_min": r.baseline_per_min,
            "incident_per_min": r.incident_per_min,
            "incident_count": r.incident_count,
        }
        if r.anomalous:
            eid = eb.add(
                EvidenceKind.SIGNAL_ANOMALY,
                identity,
                modality=Modality.LOGS,
                service=r.service,
                observed_at=r.onset,
                summary=(
                    f"{_svc(r.service)} error log rate rose to {r.incident_per_min:.1f}/min "
                    f"(baseline {r.baseline_per_min:.1f}/min)"
                ),
                attributes=attrs,
                sources=_merged(log_sources.get(r.service)),
            )
            f.anomalies.append(Anomaly(eid, Modality.LOGS, "error", r.onset, True, "up"))
        else:
            eid = eb.add(
                EvidenceKind.SIGNAL_NORMAL,
                identity,
                modality=Modality.LOGS,
                service=r.service,
                observed_at=None,
                summary=(
                    f"{_svc(r.service)} error log rate {r.incident_per_min:.1f}/min "
                    f"(baseline {r.baseline_per_min:.1f}/min): not elevated"
                ),
                attributes=attrs,
                sources=_merged(log_sources.get(r.service)),
            )
            f.normals.append((eid, "error", Modality.LOGS))


def add_change_evidence(
    eb: EvidenceBuilder,
    facts: dict[str, ServiceFacts],
    services: set[str],
    changes: list[ChangeEvent],
) -> None:
    for c in sorted(changes, key=lambda x: (x.ts, x.source_id)):
        eid = eb.add(
            EvidenceKind.CHANGE,
            {"source_id": c.source_id, "provider": c.provenance.provider},
            modality=Modality.CHANGES,
            service=c.service,
            observed_at=c.ts,
            summary=(
                f"{c.kind.value} change on {_svc(c.service or 'unknown service')} at "
                f"{c.ts.isoformat()}: {single_line(c.summary)[:200]}"
            ),
            attributes={
                "kind": c.kind.value,
                "scope": dict(sorted(c.scope.items())),
                "instances": sorted(c.instances)[:20],
                "environment": c.environment,
                "source_id": c.source_id,
            },
            sources=[{"provider": c.provenance.provider, "query": c.provenance.query or ""}],
        )
        if c.service:
            facts_for(services, facts, c.service).changes.append((eid, c))


def add_edge_change_evidence(
    eb: EvidenceBuilder,
    facts: dict[str, ServiceFacts],
    services: set[str],
    edge_changes: list[EdgeChange],
    trace_sources: dict[str, Sources] | None = None,
) -> None:
    trace_sources = trace_sources or {}
    for e in edge_changes:
        eid = eb.add(
            EvidenceKind.EDGE_CHANGE,
            {"edge": [e.source, e.target], "change": e.change},
            modality=Modality.TRACES,
            service=e.target,
            observed_at=None,
            summary=(
                f"Calls {_svc(e.source)} -> {_svc(e.target)} {e.change}: "
                f"{e.incident_per_trace:.2f} per traced request (baseline {e.baseline_per_trace:.2f})"
            ),
            attributes={
                "source": e.source,
                "target": e.target,
                "change": e.change,
                "baseline_calls": e.baseline_calls,
                "incident_calls": e.incident_calls,
            },
            sources=_merged(trace_sources.get(e.source)),
        )
        eb.relate(f"svc:{e.source}", f"svc:{e.target}", RelationKind.CALLS)
        facts_for(services, facts, e.target).edge_change_evidence.append(eid)
        facts_for(services, facts, e.source).edge_change_evidence.append(eid)


def add_coverage_evidence(
    eb: EvidenceBuilder,
    snapshot: TelemetrySnapshot,
    origin: OriginStats,
    has_parent_links: bool,
    graph: Graph,
) -> list[str]:
    gaps: list[str] = []
    trace_providers = _merged(*sources_by_service(snapshot.spans).values())
    for modality in (Modality.METRICS, Modality.LOGS, Modality.TRACES, Modality.CHANGES):
        status = snapshot.outcome(modality)
        outcome_sources = [
            {"provider": o.provider, "query": f"collection status: {o.status.value}"}
            for o in snapshot.collection
            if o.modality is modality
        ][:3] or [{"provider": "aegis", "query": f"collection plan: no {modality.value} source"}]
        details = [o.detail for o in snapshot.collection if o.modality is modality and o.detail]
        truncated = any(o.truncated for o in snapshot.collection if o.modality is modality)
        if status is not CollectionStatus.OK or truncated:
            text = {
                CollectionStatus.NOT_CONFIGURED: f"No {modality.value} source configured",
                CollectionStatus.FAILED: f"{modality.value} collection failed",
                CollectionStatus.PARTIAL: f"{modality.value} collection was partial",
                CollectionStatus.OK: f"{modality.value} results were truncated",
            }[status]
            gaps.append(text)
            eb.add(
                EvidenceKind.COVERAGE_GAP,
                {"modality": modality.value, "status": status.value, "truncated": truncated},
                modality=modality,
                service=None,
                observed_at=None,
                summary=text + (f" ({'; '.join(details)[:300]})" if details else ""),
                attributes={
                    "modality": modality.value,
                    "status": status.value,
                    "truncated": truncated,
                },
                sources=outcome_sources,
            )
    if origin.total_traces and origin.traces_with_unknown_status == origin.total_traces:
        text = "Span status is unknown for every trace: error origins and propagation cannot be evaluated"
        gaps.append(text)
        eb.add(
            EvidenceKind.COVERAGE_GAP,
            {"gap": "span_status_unknown"},
            modality=Modality.TRACES,
            service=None,
            observed_at=None,
            summary=text,
            attributes={"traces": origin.total_traces},
            sources=trace_providers,
        )
    if snapshot.spans and not has_parent_links and not graph.derived_from_traces:
        text = "Traces carry no parent links: service dependencies cannot be derived from traces"
        gaps.append(text)
        eb.add(
            EvidenceKind.COVERAGE_GAP,
            {"gap": "no_parent_links"},
            modality=Modality.TRACES,
            service=None,
            observed_at=None,
            summary=text,
            attributes={},
            sources=trace_providers,
        )
    return gaps


def relevant_changes(snapshot: TelemetrySnapshot, request: AnalysisRequest) -> list[ChangeEvent]:
    lookback = timedelta(minutes=request.tolerances.change_lookback_minutes)
    start = request.incident.start - lookback
    return [c for c in snapshot.changes if start <= c.ts < request.incident.end]


def services_of(snapshot: TelemetrySnapshot) -> set[str]:
    names = {m.service for m in snapshot.metrics}
    names |= {s.service for s in snapshot.spans}
    names |= {r.service for r in snapshot.logs}
    names |= {e.source for e in snapshot.edges} | {e.target for e in snapshot.edges}
    return names


def _origin_onset(
    origin: OriginStats,
    by_trace: dict[str, TraceGroup],
    service: str,
    base_rate: float | None,
    request: AnalysisRequest,
) -> datetime | None:
    """Onset of an *elevated* error-origin rate, not the first background failure.

    Buckets incident traces by start time and returns the first bucket whose origin rate
    exceeds max(3 x baseline rate, 5%), confirmed by 2 of the 3 buckets starting there.
    """
    size = request.tolerances.bucket_seconds
    totals: dict[datetime, int] = defaultdict(int)
    hits: dict[datetime, int] = defaultdict(int)
    for g in by_trace.values():
        start = g.spans[0].start
        epoch = start.timestamp()
        key = datetime.fromtimestamp(epoch - (epoch % size), tz=start.tzinfo)
        totals[key] += 1
        if service in origin.origins_by_trace.get(g.trace_id, frozenset()):
            hits[key] += 1
    threshold = max(3 * (base_rate or 0.0), 0.05)
    keys = sorted(totals)
    flags = [hits[k] / totals[k] >= threshold for k in keys]
    for i, flag in enumerate(flags):
        window = flags[i : i + 3]
        if flag and sum(window) >= min(2, len(window)):
            return keys[i]
    return None
