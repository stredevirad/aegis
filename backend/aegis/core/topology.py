"""Service graph: configured edges plus caller->callee edges derived from traces.

Also computes the per-edge call-pattern diff between baseline and incident, which surfaces
masked faults (a fallback path changes *who calls whom* even when no errors are emitted).
Call counts are normalised per traced request because traces are sampled.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from aegis.core.model import Tolerances, TopologyEdge
from aegis.core.traces import EdgeCounts


@dataclass
class Graph:
    callees: dict[str, set[str]]
    callers: dict[str, set[str]]
    derived_from_traces: bool
    inferred_services: frozenset[str]

    @classmethod
    def build(
        cls,
        configured: list[TopologyEdge],
        observed: list[EdgeCounts],
        services: set[str],
    ) -> Graph:
        callees: dict[str, set[str]] = defaultdict(set)
        callers: dict[str, set[str]] = defaultdict(set)
        inferred: set[str] = set()
        for e in configured:
            if e.source != e.target:
                callees[e.source].add(e.target)
                callers[e.target].add(e.source)
        derived = False
        for counts in observed:
            inferred |= counts.inferred_targets
            for (src, dst), n in counts.calls.items():
                if n > 0 and src != dst:
                    derived = True
                    callees[src].add(dst)
                    callers[dst].add(src)
        for s in services:
            callees.setdefault(s, set())
            callers.setdefault(s, set())
        return cls(dict(callees), dict(callers), derived, frozenset(inferred))

    def downstream(self, service: str) -> set[str]:
        """All services reachable from ``service`` (transitive callees)."""
        return self._reach(service, self.callees)

    def upstream(self, service: str) -> set[str]:
        return self._reach(service, self.callers)

    @staticmethod
    def _reach(start: str, adj: dict[str, set[str]]) -> set[str]:
        seen: set[str] = set()
        queue = deque(sorted(adj.get(start, set())))
        while queue:
            node = queue.popleft()
            if node in seen or node == start:
                continue
            seen.add(node)
            queue.extend(sorted(adj.get(node, set()) - seen))
        return seen

    def connected(self, a: str, b: str) -> bool:
        return b in self.downstream(a) or a in self.downstream(b)

    def components(self, nodes: set[str]) -> list[set[str]]:
        """Groups of ``nodes`` linked by a dependency path (through any services)."""
        remaining = set(nodes)
        groups: list[set[str]] = []
        while remaining:
            seed = min(remaining)
            group = {seed}
            changed = True
            while changed:
                changed = False
                for other in sorted(remaining - group):
                    if any(self.connected(other, g) for g in group):
                        group.add(other)
                        changed = True
            groups.append(group)
            remaining -= group
        return sorted(groups, key=lambda g: min(g))


@dataclass(frozen=True)
class EdgeChange:
    source: str
    target: str
    baseline_calls: int
    incident_calls: int
    baseline_per_trace: float
    incident_per_trace: float
    change: str  # "new" | "vanished" | "increased" | "decreased"


def edge_diff(baseline: EdgeCounts, incident: EdgeCounts, tol: Tolerances) -> list[EdgeChange]:
    if baseline.traces == 0 or incident.traces == 0:
        return []
    changes: list[EdgeChange] = []
    callers_with_traffic_base = {s for (s, _), n in baseline.calls.items() if n > 0}
    callers_with_traffic_inc = {s for (s, _), n in incident.calls.items() if n > 0}
    for edge in sorted(set(baseline.calls) | set(incident.calls)):
        b = baseline.calls.get(edge, 0)
        i = incident.calls.get(edge, 0)
        b_pt = b / baseline.traces
        i_pt = i / incident.traces
        src = edge[0]
        kind: str | None = None
        if b == 0 and i >= tol.min_edge_calls and src in callers_with_traffic_base:
            kind = "new"
        elif i == 0 and b >= tol.min_edge_calls and src in callers_with_traffic_inc:
            kind = "vanished"
        elif b >= tol.min_edge_calls and i >= tol.min_edge_calls and b_pt > 0:
            ratio = i_pt / b_pt
            if ratio >= tol.edge_change_ratio:
                kind = "increased"
            elif ratio <= 1 / tol.edge_change_ratio:
                kind = "decreased"
        if kind:
            changes.append(EdgeChange(edge[0], edge[1], b, i, b_pt, i_pt, kind))
    return changes
