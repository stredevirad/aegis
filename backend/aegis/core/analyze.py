"""The pure RCA entry point: ``analyze(snapshot, request) -> Analysis``.

No I/O, no clock, no randomness (ADR-008). Given the same snapshot and request the output is
identical, which is what makes replay, the benchmark and audits trustworthy.
"""

from __future__ import annotations

from aegis.core import facts as F
from aegis.core.decide import Context, decide
from aegis.core.logs import analyze_logs
from aegis.core.model import AnalysisRequest, CollectionStatus, Modality, TelemetrySnapshot
from aegis.core.result import Analysis, Candidate, Evidence, EvidenceKind, Verdict, digest_of
from aegis.core.signals import assess, extract_signals
from aegis.core.topology import Graph, edge_diff
from aegis.core.traces import (
    co_occurrence,
    edges_from_spans,
    error_origins,
    group_traces,
    has_parent_links,
    latency_origins,
    root_duration_threshold,
)

# Evidence kinds dropped first when the evidence cap is reached.
_DROP_ORDER = [
    EvidenceKind.SIGNAL_NORMAL,
    EvidenceKind.LOG_SIGNATURE,
    EvidenceKind.TRACE_EXEMPLAR,
    EvidenceKind.EDGE_CHANGE,
]


def analyze(snapshot: TelemetrySnapshot, request: AnalysisRequest) -> Analysis:
    tol = request.tolerances
    eb = F.EvidenceBuilder()
    facts: dict[str, F.ServiceFacts] = {}
    services = F.services_of(snapshot)
    for s in services:
        facts[s] = F.ServiceFacts(s)
    changes = F.relevant_changes(snapshot, request)

    # Signals (metrics, or span-derived where metrics are missing).
    signals = extract_signals(snapshot.metrics, snapshot.spans, tol)
    assessments = [a for s in signals if (a := assess(s, request, snapshot.changes)) is not None]
    F.add_signal_evidence(eb, facts, services, assessments)

    # Traces.
    incident_groups = group_traces(snapshot.spans, request.incident)
    baseline_groups = []
    for window in request.baselines:
        baseline_groups = group_traces(snapshot.spans, window)
        if baseline_groups:
            break
    origin = error_origins(incident_groups)
    latency = latency_origins(
        incident_groups,
        root_duration_threshold(baseline_groups, tol.latency_ratio, tol.latency_floor_ms)
        if has_parent_links(snapshot.spans)
        else None,
    )
    base_origin = error_origins(baseline_groups)
    inc_edges = edges_from_spans(incident_groups)
    base_edges = edges_from_spans(baseline_groups)
    graph = Graph.build(snapshot.edges, [inc_edges, base_edges], services)
    links = has_parent_links(snapshot.spans)
    F.add_origin_evidence(eb, facts, services, origin, base_origin, incident_groups, request)
    F.add_edge_latency_evidence(eb, facts, services, request, base_edges, inc_edges)
    F.add_edge_change_evidence(eb, facts, services, edge_diff(base_edges, inc_edges, tol))
    for (_caller, callee), n in sorted(inc_edges.status_known.items()):
        if n > 0 and callee in graph.inferred_services and callee in facts:
            facts[callee].evaluable = True  # health of an uninstrumented peer seen by callers

    # Logs and changes.
    findings, rates = analyze_logs(snapshot.logs, request)
    F.add_log_evidence(eb, facts, services, findings, rates)
    F.add_change_evidence(eb, facts, services, changes)

    gaps = F.add_coverage_evidence(eb, snapshot, origin, links, graph)
    for s in sorted(graph.inferred_services):
        F.facts_for(services, facts, s)

    services_with_links = {s.service for s in snapshot.spans if s.parent_span_id} | {
        s.service
        for g in incident_groups
        for s in g.spans
        if any(c for c in g.children.get(s.span_id, []))
    }
    ctx = Context(
        request=request,
        graph=graph,
        facts=facts,
        origin=origin,
        latency=latency,
        co_occurrence=co_occurrence(incident_groups) if not links else {},
        change_feed_ok=snapshot.outcome(Modality.CHANGES)
        in (CollectionStatus.OK, CollectionStatus.PARTIAL),
        own_telemetry=F.services_of(snapshot),
        services_with_links=services_with_links,
        configured_sources={e.source for e in snapshot.edges},
        eb=eb,
        coverage_gaps=gaps,
        valid_baselines=sum(1 for a in assessments if a.baseline_valid),
    )
    candidates, verdict = decide(ctx)

    evidence = _cap(list(eb.items.values()), candidates, verdict, tol.max_evidence_items, ctx.trace)
    kept = {e.id for e in evidence}
    relations = [
        r
        for r in eb.relation_list()
        if (r.source in kept or not r.source.startswith("ev-"))
        and (r.target in kept or not r.target.startswith("ev-"))
    ]
    return Analysis(
        request=request,
        snapshot_digest=digest_of(snapshot.model_dump(mode="json")),
        evidence=evidence,
        relations=relations,
        candidates=candidates,
        verdict=verdict,
        decision_trace=ctx.trace,
    )


def _cap(
    evidence: list[Evidence],
    candidates: list[Candidate],
    verdict: Verdict,
    limit: int,
    trace: list[str],
) -> list[Evidence]:
    """Bound the evidence list without dropping anything a conclusion relies on."""
    referenced: set[str] = set()
    for c in candidates:
        referenced |= set(c.supporting_evidence_ids) | set(c.contradicting_evidence_ids)
        for ch in c.checks:
            referenced |= set(ch.evidence_ids)
    for rc in verdict.root_causes:
        referenced |= set(rc.supporting_evidence_ids) | set(rc.contradicting_evidence_ids)
    ordered = sorted(evidence, key=lambda e: (e.kind.value, e.service or "", e.id))
    if len(ordered) <= limit:
        return ordered
    keep = [e for e in ordered if e.id in referenced or e.kind not in _DROP_ORDER]
    for kind in reversed(_DROP_ORDER):
        extra = [e for e in ordered if e.kind is kind and e.id not in referenced]
        room = limit - len(keep)
        if room <= 0:
            break
        keep += extra[:room]
    dropped = len(ordered) - len(keep)
    if dropped:
        trace.append(f"evidence cap {limit}: {dropped} unreferenced item(s) omitted")
    return sorted(keep, key=lambda e: (e.kind.value, e.service or "", e.id))
