"""Trace analysis: error origins, service call edges and co-occurrence.

Error origin: in a failing trace, the deepest failing span whose children are all known to
be healthy. If a failing span has a child whose status is unknown, the origin of that branch
is undetermined (never assumed healthy). A failing CLIENT span without collected children
points at its peer (e.g. an uninstrumented database); such origins are marked inferred.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from aegis.core.model import Span, SpanKind, SpanStatus, TimeWindow


@dataclass
class TraceGroup:
    trace_id: str
    spans: list[Span]
    children: dict[str, list[Span]]

    @property
    def failing(self) -> bool:
        return any(s.status is SpanStatus.ERROR for s in self.spans)

    @property
    def status_known(self) -> bool:
        return all(s.status is not SpanStatus.UNKNOWN for s in self.spans)


@dataclass
class OriginStats:
    failing_traces: int = 0
    determined_traces: int = 0
    undetermined_traces: int = 0
    traces_with_unknown_status: int = 0
    total_traces: int = 0
    # service -> number of failing traces whose origin set contains it
    origin_counts: Counter[str] = field(default_factory=Counter)
    inferred_origins: set[str] = field(default_factory=set)
    # service -> failing trace ids where the service had a failing span (symptom or origin)
    failing_in: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    # trace id -> origin services
    origins_by_trace: dict[str, frozenset[str]] = field(default_factory=dict)

    def share(self, service: str) -> float | None:
        if self.determined_traces == 0:
            return None
        return self.origin_counts[service] / self.determined_traces


def group_traces(spans: list[Span], window: TimeWindow) -> list[TraceGroup]:
    by_trace: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        by_trace[s.trace_id].append(s)
    groups: list[TraceGroup] = []
    for trace_id in sorted(by_trace):
        members = sorted(by_trace[trace_id], key=lambda s: (s.start, s.span_id))
        if not any(window.contains(s.start) for s in members):
            continue
        children: dict[str, list[Span]] = defaultdict(list)
        for s in members:
            if s.parent_span_id:
                children[s.parent_span_id].append(s)
        groups.append(TraceGroup(trace_id, members, children))
    return groups


def _origins(group: TraceGroup) -> tuple[set[str], set[str], bool]:
    """Return (origin services, inferred origin services, determined?) for a failing trace."""
    origins: set[str] = set()
    inferred: set[str] = set()
    determined = True
    for s in group.spans:
        if s.status is not SpanStatus.ERROR:
            continue
        kids = group.children.get(s.span_id, [])
        if any(k.status is SpanStatus.ERROR for k in kids):
            continue  # not the deepest failing span on this branch
        if any(k.status is SpanStatus.UNKNOWN for k in kids):
            determined = False
            continue
        if not kids and s.kind is SpanKind.CLIENT and s.peer_service:
            origins.add(s.peer_service)
            inferred.add(s.peer_service)
        else:
            origins.add(s.service)
    if not origins:
        determined = False
    return origins, inferred, determined


def error_origins(groups: list[TraceGroup]) -> OriginStats:
    stats = OriginStats()
    for g in groups:
        stats.total_traces += 1
        if any(s.status is SpanStatus.UNKNOWN for s in g.spans):
            stats.traces_with_unknown_status += 1
        if not g.failing:
            continue
        stats.failing_traces += 1
        for s in g.spans:
            if s.status is SpanStatus.ERROR:
                stats.failing_in[s.service].add(g.trace_id)
        origins, inferred, determined = _origins(g)
        if not determined:
            stats.undetermined_traces += 1
            continue
        stats.determined_traces += 1
        stats.origins_by_trace[g.trace_id] = frozenset(origins)
        for o in origins:
            stats.origin_counts[o] += 1
        stats.inferred_origins |= inferred
    return stats


@dataclass(frozen=True)
class EdgeCounts:
    calls: Counter[tuple[str, str]]
    errors: Counter[tuple[str, str]]
    traces: int
    inferred_targets: frozenset[str]
    # (caller, callee) -> (span start, duration in ms) of the calls, for edge latency
    latencies: dict[tuple[str, str], list[tuple[datetime, float]]]
    # (caller, callee) -> number of calls whose status is known
    status_known: Counter[tuple[str, str]]


def edges_from_spans(groups: list[TraceGroup]) -> EdgeCounts:
    """Caller -> callee service edges from parent/child links and client peers."""
    calls: Counter[tuple[str, str]] = Counter()
    errors: Counter[tuple[str, str]] = Counter()
    known: Counter[tuple[str, str]] = Counter()
    latencies: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    inferred: set[str] = set()

    def record(edge: tuple[str, str], span: Span) -> None:
        calls[edge] += 1
        latencies[edge].append((span.start, span.duration_ms))
        if span.status is not SpanStatus.UNKNOWN:
            known[edge] += 1
        if span.status is SpanStatus.ERROR:
            errors[edge] += 1

    for g in groups:
        by_id = {s.span_id: s for s in g.spans}
        for s in g.spans:
            parent = by_id.get(s.parent_span_id) if s.parent_span_id else None
            if parent is not None and parent.service != s.service:
                record((parent.service, s.service), s)
            if (
                s.kind is SpanKind.CLIENT
                and s.peer_service
                and s.peer_service != s.service
                and not any(k.service != s.service for k in g.children.get(s.span_id, []))
            ):
                record((s.service, s.peer_service), s)
                inferred.add(s.peer_service)
    return EdgeCounts(calls, errors, len(groups), frozenset(inferred), dict(latencies), known)


def co_occurrence(groups: list[TraceGroup]) -> dict[str, set[str]]:
    """Services seen in the same traces. Used only when parent links are unavailable."""
    seen: dict[str, set[str]] = defaultdict(set)
    for g in groups:
        services = {s.service for s in g.spans}
        for svc in services:
            seen[svc] |= services - {svc}
    return seen


def has_parent_links(spans: list[Span]) -> bool:
    return any(s.parent_span_id for s in spans)


def exemplar_traces(stats: OriginStats, service: str, limit: int = 3) -> list[str]:
    ids = sorted(t for t, origins in stats.origins_by_trace.items() if service in origins)
    return ids[:limit]


@dataclass
class LatencyStats:
    threshold_ms: float | None
    slow_traces: int = 0
    # service -> number of slow traces in which it accounts for the most exclusive time
    dominant_counts: Counter[str] = field(default_factory=Counter)
    slow_by_trace: dict[str, str] = field(default_factory=dict)  # trace id -> dominant service

    def share(self, service: str) -> float | None:
        if self.slow_traces == 0:
            return None
        return self.dominant_counts[service] / self.slow_traces


def _root(group: TraceGroup) -> Span:
    ids = {s.span_id for s in group.spans}
    roots = [s for s in group.spans if not s.parent_span_id or s.parent_span_id not in ids]
    return max(roots or group.spans, key=lambda s: s.duration_ms)


def root_duration_threshold(
    baseline: list[TraceGroup], latency_ratio: float, floor_ms: float
) -> float | None:
    """Duration above which an incident trace counts as slow (None without a baseline)."""
    durations = sorted(_root(g).duration_ms for g in baseline)
    if len(durations) < 5:
        return None
    p95 = durations[min(len(durations) - 1, int(0.95 * len(durations)))]
    return max(p95 * latency_ratio, p95 + floor_ms)


def latency_origins(groups: list[TraceGroup], threshold_ms: float | None) -> LatencyStats:
    """For slow traces, the service accounting for the most exclusive (self) time.

    Exclusive time of a span = its duration minus its children's durations. A CLIENT span
    without collected children is time spent in its peer, so it is attributed to the peer
    (this is how an uninstrumented database shows up). Requires parent links.
    """
    stats = LatencyStats(threshold_ms=threshold_ms)
    if threshold_ms is None:
        return stats
    for g in groups:
        if _root(g).duration_ms < threshold_ms:
            continue
        excl: dict[str, float] = defaultdict(float)
        for s in g.spans:
            kids = g.children.get(s.span_id, [])
            own = max(0.0, s.duration_ms - sum(k.duration_ms for k in kids))
            to_peer = s.kind is SpanKind.CLIENT and s.peer_service and not kids
            excl[s.peer_service if to_peer and s.peer_service else s.service] += own
        if not excl:
            continue
        dominant = max(sorted(excl), key=lambda k: excl[k])
        stats.slow_traces += 1
        stats.dominant_counts[dominant] += 1
        stats.slow_by_trace[g.trace_id] = dominant
    return stats
