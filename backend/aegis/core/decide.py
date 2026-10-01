"""Candidate ranking (ordered decision list), tri-state checks and the deterministic verdict.

Rules are evaluated in order; the first rule that selects a leader wins. Every rule is one
sentence so an operator can see exactly why a service was blamed. Verdict rules follow
ADR-009 (amendment 1). ``not_evaluable`` and ``not_applicable`` never count as a pass.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aegis.core.facts import EvidenceBuilder, ServiceFacts
from aegis.core.model import AnalysisRequest, ChangeEvent
from aegis.core.result import (
    SUPPORT_ORDER,
    Candidate,
    Check,
    CheckName,
    CheckState,
    Classification,
    HypothesisKind,
    Mechanism,
    NextCheck,
    RelationKind,
    RootCause,
    Support,
    Verdict,
)
from aegis.core.topology import Graph
from aegis.core.traces import LatencyStats, OriginStats

RULES: dict[str, str] = {
    "R1": "A service in which at least half of the failing traces end (deepest failing span) leads.",
    "R1b": (
        "Services that are each the origin of a substantial share of failing traces and do not "
        "depend on each other lead together (independent faults)."
    ),
    "R1L": (
        "A service that accounts for most of the time spent in at least half of the slow "
        "traces leads."
    ),
    "R2": (
        "An anomalous service with a recorded change shortly before its onset and no anomalous "
        "service downstream of it leads."
    ),
    "R3": "The anomalous service that every other anomalous service depends on leads.",
    "R4": (
        "A dependency whose call pattern changed during the incident and that lies downstream "
        "of every anomalous service leads (possible masked failure)."
    ),
    "R5": (
        "The anomalous service whose onset precedes every other anomalous service by more than "
        "the tolerance band leads."
    ),
    "R6": "If exactly one service is anomalous, it leads.",
}

SUBSTANTIVE = (
    CheckName.TEMPORAL_PRECEDENCE,
    CheckName.PROPAGATION,
    CheckName.ISOLATION,
    CheckName.CHANGE_SCOPE,
    CheckName.MODALITY_AGREEMENT,
)


@dataclass
class Context:
    request: AnalysisRequest
    graph: Graph
    facts: dict[str, ServiceFacts]
    origin: OriginStats
    latency: LatencyStats
    co_occurrence: dict[str, Counter[str]]
    change_feed_ok: bool
    own_telemetry: set[str]
    services_with_links: set[str]
    configured_sources: set[str]
    eb: EvidenceBuilder
    coverage_gaps: list[str]
    valid_baselines: int = 0
    trace: list[str] = field(default_factory=list)

    @property
    def tolerance(self) -> timedelta:
        return timedelta(seconds=self.request.tolerances.precedence_tolerance_seconds)

    @property
    def lookback(self) -> timedelta:
        return timedelta(minutes=self.request.tolerances.change_lookback_minutes)

    def anomalous(self) -> set[str]:
        return {s for s, f in self.facts.items() if f.anomalous}

    def topology_known(self, service: str) -> bool:
        return (
            service in self.services_with_links
            or service in self.configured_sources
            or (service in self.graph.inferred_services and service not in self.own_telemetry)
        )

    def evaluable(self, service: str) -> bool:
        f = self.facts.get(service)
        return f is not None and f.evaluable


# ---------------------------------------------------------------------------------------
# Candidate pool and decision list
# ---------------------------------------------------------------------------------------


def _pool(ctx: Context) -> tuple[set[str], set[str]]:
    anomalous = ctx.anomalous()
    pool = set(anomalous)
    for s, f in ctx.facts.items():
        downstream_of_anomalous = any(s in ctx.graph.downstream(a) for a in anomalous)
        if f.edge_change_evidence and (downstream_of_anomalous or not anomalous):
            pool.add(s)
        if f.changes and (s in anomalous or downstream_of_anomalous):
            pool.add(s)
    focus = set(ctx.request.focus_services)
    components = ctx.graph.components(pool)
    focus_known = any(ctx.graph.callees.get(f) or ctx.graph.callers.get(f) for f in focus)
    if not focus or not focus_known:
        # Without topology for the focus service, "not connected" means "unknown", not
        # "unrelated": keep every anomaly in scope.
        return pool, set()
    primary: set[str] = set()
    for comp in components:
        if comp & focus or any(ctx.graph.connected(f, c) for f in focus for c in comp):
            primary |= comp
    if not primary:
        return pool, set()
    return primary, pool - primary


def _change_before_onset(ctx: Context, f: ServiceFacts) -> ChangeEvent | None:
    onset = f.onset or ctx.request.incident.end
    eligible = [c for _, c in f.changes if c.ts <= onset and onset - c.ts <= ctx.lookback]
    return max(eligible, key=lambda c: (c.ts, c.source_id)) if eligible else None


def _decide_component(ctx: Context, comp: set[str]) -> list[tuple[str, str]]:
    anomalous = comp & ctx.anomalous()
    tol = ctx.request.tolerances
    ctx.trace.append(f"component {sorted(comp)}: anomalous {sorted(anomalous)}")

    shares = {s: ctx.facts[s].origin_share or 0.0 for s in comp if ctx.facts[s].origin_is_anomalous}
    r1 = [s for s, v in shares.items() if v >= tol.error_origin_lead_share]
    if len(r1) == 1:
        return [(r1[0], "R1")]
    if len(r1) > 1:
        # several origins above half: keep the ones with no anomalous origin downstream
        downstream_most = [s for s in r1 if not (ctx.graph.downstream(s) & set(r1))]
        return [(s, "R1") for s in sorted(downstream_most)]
    substantial = sorted(s for s, v in shares.items() if v >= 0.25)
    if len(substantial) >= 2 and all(
        not ctx.graph.connected(a, b) for a in substantial for b in substantial if a < b
    ):
        return [(s, "R1b") for s in substantial]
    ctx.trace.append("R1/R1b: no dominant error origin")

    r1l = sorted(
        s
        for s in comp & anomalous
        if (ctx.latency.share(s) or 0.0) >= tol.error_origin_lead_share
        and ctx.latency.dominant_counts[s] >= tol.min_signature_count
    )
    if len(r1l) == 1:
        return [(r1l[0], "R1L")]
    ctx.trace.append("R1L: no service dominates the slow traces")

    r2 = sorted(
        s
        for s in anomalous
        if _change_before_onset(ctx, ctx.facts[s]) is not None
        and not (ctx.graph.downstream(s) & anomalous)
    )
    if r2:
        return [(s, "R2") for s in r2]
    ctx.trace.append("R2: no anomalous downstream-most service with a preceding change")

    if len(anomalous) >= 2:
        r3 = sorted(s for s in anomalous if (anomalous - {s}) <= ctx.graph.upstream(s))
        if len(r3) == 1:
            return [(r3[0], "R3")]
        ctx.trace.append("R3: no single common dependency of all anomalous services")

    if anomalous:
        behaviour = sorted(
            s
            for s in comp - anomalous
            if ctx.facts[s].edge_change_evidence and anomalous <= ctx.graph.upstream(s)
        )
        if len(behaviour) == 1:
            return [(behaviour[0], "R4")]
        ctx.trace.append("R4: no single changed dependency downstream of all anomalous services")

    timed: list[tuple[datetime, str]] = sorted(
        (onset, s) for s in anomalous if (onset := ctx.facts[s].onset) is not None
    )
    if len(timed) >= 2 and timed[0][0] + ctx.tolerance < timed[1][0]:
        return [(timed[0][1], "R5")]
    ctx.trace.append("R5: onsets do not separate a single earliest service")

    if len(anomalous) == 1:
        return [(next(iter(anomalous)), "R6")]
    ctx.trace.append("R6: not exactly one anomalous service; no leader")
    return []


# ---------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------


def _check(name: CheckName, state: CheckState, reason: str, ids: list[str] | None = None) -> Check:
    return Check(name=name, state=state, reason=reason, evidence_ids=sorted(set(ids or [])))


def _checks(ctx: Context, service: str, leaders: set[str]) -> list[Check]:
    f = ctx.facts[service]
    tol = ctx.request.tolerances
    anomalous = ctx.anomalous()
    dependents = anomalous & ctx.graph.upstream(service)
    out: list[Check] = []

    # Baseline validity of the anomalies the claim rests on.
    if not f.anomalies:
        out.append(
            _check(
                CheckName.BASELINE_VALID,
                CheckState.NOT_EVALUABLE,
                "no baselined signal anomaly for this service",
            )
        )
    elif all(a.baseline_valid for a in f.anomalies):
        out.append(
            _check(
                CheckName.BASELINE_VALID,
                CheckState.PASS,
                "every anomaly was measured against a valid baseline",
                [a.evidence_id for a in f.anomalies],
            )
        )
    else:
        out.append(
            _check(
                CheckName.BASELINE_VALID,
                CheckState.NOT_EVALUABLE,
                "at least one anomaly has no valid baseline (absolute threshold used)",
                [a.evidence_id for a in f.anomalies if not a.baseline_valid],
            )
        )

    # Temporal precedence over anomalous dependents.
    if not dependents:
        out.append(
            _check(
                CheckName.TEMPORAL_PRECEDENCE,
                CheckState.NOT_APPLICABLE,
                "no anomalous service depends on it",
            )
        )
    elif f.onset is None:
        out.append(
            _check(
                CheckName.TEMPORAL_PRECEDENCE,
                CheckState.NOT_EVALUABLE,
                "onset of this service could not be determined",
            )
        )
    else:
        timed = {d: ctx.facts[d].onset for d in dependents if ctx.facts[d].onset is not None}
        earlier = sorted(
            d for d, o in timed.items() if o is not None and o < f.onset - ctx.tolerance
        )
        if not timed:
            out.append(
                _check(
                    CheckName.TEMPORAL_PRECEDENCE,
                    CheckState.NOT_EVALUABLE,
                    "onsets of dependent services could not be determined",
                )
            )
        elif earlier:
            ids = [a.evidence_id for d in earlier for a in ctx.facts[d].anomalies]
            out.append(
                _check(
                    CheckName.TEMPORAL_PRECEDENCE,
                    CheckState.FAIL,
                    f"dependents {earlier} became anomalous before it",
                    ids,
                )
            )
        elif all(o is not None and o >= f.onset + ctx.tolerance for o in timed.values()):
            ids = [a.evidence_id for a in f.anomalies]
            out.append(
                _check(
                    CheckName.TEMPORAL_PRECEDENCE,
                    CheckState.PASS,
                    "it became anomalous before every anomalous dependent "
                    f"(by more than {tol.precedence_tolerance_seconds}s)",
                    ids,
                )
            )
        else:
            out.append(
                Check(
                    name=CheckName.TEMPORAL_PRECEDENCE,
                    state=CheckState.NOT_EVALUABLE,
                    reason="onsets are within the tolerance band (tie)",
                    qualifier="tie",
                )
            )

    # Propagation, by failure family: errors use error origins, latency/saturation use
    # latency attribution. A handful of background traces must never decide this check.
    fams = f.families()
    min_traces = tol.min_signature_count
    if "error" in fams:
        involved = {service} | dependents
        relevant = [
            t
            for t, origins in ctx.origin.origins_by_trace.items()
            if service in origins or any(t in ctx.origin.failing_in.get(d, set()) for d in involved)
        ]
        ids = [f.origin_evidence] if f.origin_evidence else []
        if ctx.origin.determined_traces == 0:
            out.append(
                _check(
                    CheckName.PROPAGATION,
                    CheckState.NOT_EVALUABLE,
                    "no failing traces with known span status",
                )
            )
        elif len(relevant) < min_traces:
            out.append(
                _check(
                    CheckName.PROPAGATION,
                    CheckState.NOT_EVALUABLE,
                    f"only {len(relevant)} failing trace(s) involve it or its dependents",
                )
            )
        else:
            hits = sum(1 for t in relevant if service in ctx.origin.origins_by_trace[t])
            share = hits / len(relevant)
            if share >= tol.propagation_pass_share:
                out.append(
                    _check(
                        CheckName.PROPAGATION,
                        CheckState.PASS,
                        f"{hits}/{len(relevant)} relevant failing traces end in it",
                        ids,
                    )
                )
            elif share <= tol.propagation_fail_share:
                others = sorted(
                    {o for t in relevant for o in ctx.origin.origins_by_trace[t]} - {service}
                )
                other_ids = [ctx.facts[o].origin_evidence or "" for o in others if o in ctx.facts]
                out.append(
                    _check(
                        CheckName.PROPAGATION,
                        CheckState.FAIL,
                        f"only {hits}/{len(relevant)} relevant failing traces end in it; "
                        f"others end in {others[:5]}",
                        [i for i in other_ids if i],
                    )
                )
            else:
                out.append(
                    _check(
                        CheckName.PROPAGATION,
                        CheckState.NOT_EVALUABLE,
                        f"mixed: {hits}/{len(relevant)} relevant failing traces end in it",
                        ids,
                    )
                )
    elif fams & {"latency", "saturation"}:
        lat = ctx.latency
        ids = [a.evidence_id for a in f.anomalies if a.family in ("latency", "saturation")]
        if lat.threshold_ms is None:
            out.append(
                _check(
                    CheckName.PROPAGATION,
                    CheckState.NOT_EVALUABLE,
                    "no trace baseline (or no parent links) to attribute latency",
                )
            )
        elif lat.slow_traces < min_traces:
            out.append(
                _check(
                    CheckName.PROPAGATION,
                    CheckState.NOT_EVALUABLE,
                    f"only {lat.slow_traces} slow trace(s) to attribute",
                )
            )
        else:
            hits = lat.dominant_counts[service]
            share = hits / lat.slow_traces
            if share >= tol.propagation_pass_share:
                out.append(
                    _check(
                        CheckName.PROPAGATION,
                        CheckState.PASS,
                        f"it accounts for most of the time in {hits}/{lat.slow_traces} slow traces",
                        ids,
                    )
                )
            elif share <= tol.propagation_fail_share:
                top = [s for s, _ in lat.dominant_counts.most_common(3)]
                out.append(
                    _check(
                        CheckName.PROPAGATION,
                        CheckState.FAIL,
                        f"it dominates only {hits}/{lat.slow_traces} slow traces; "
                        f"most time is spent in {top}",
                    )
                )
            else:
                out.append(
                    _check(
                        CheckName.PROPAGATION,
                        CheckState.NOT_EVALUABLE,
                        f"mixed: it dominates {hits}/{lat.slow_traces} slow traces",
                        ids,
                    )
                )
    else:
        out.append(
            _check(
                CheckName.PROPAGATION,
                CheckState.NOT_APPLICABLE,
                "no error or latency anomaly to trace",
            )
        )

    # Isolation and downstream evaluation.
    callees = sorted(ctx.graph.callees.get(service, set()))
    if not ctx.topology_known(service):
        seen_with = _ranked_co_occurring(ctx, service)
        out.append(
            _check(
                CheckName.ISOLATION,
                CheckState.NOT_EVALUABLE,
                "its dependencies are unknown (no parent links or configured edges)",
            )
        )
        out.append(
            _check(
                CheckName.DOWNSTREAM_EVALUATED,
                CheckState.NOT_EVALUABLE,
                "dependencies unknown"
                + (f"; services seen in the same traces: {seen_with[:10]}" if seen_with else ""),
            )
        )
    else:
        anomalous_callees = [c for c in callees if c in anomalous]
        unevaluated = [c for c in callees if not ctx.evaluable(c)]
        if anomalous_callees:
            ids = [a.evidence_id for c in anomalous_callees for a in ctx.facts[c].anomalies]
            out.append(
                _check(
                    CheckName.ISOLATION,
                    CheckState.FAIL,
                    f"dependencies {anomalous_callees} are also anomalous",
                    ids,
                )
            )
        elif unevaluated:
            out.append(
                _check(
                    CheckName.ISOLATION,
                    CheckState.NOT_EVALUABLE,
                    f"dependencies {unevaluated} have no telemetry with known health",
                )
            )
        else:
            ids = [i for c in callees for i, _, _ in ctx.facts[c].normals] if callees else []
            out.append(
                _check(
                    CheckName.ISOLATION,
                    CheckState.PASS,
                    "all of its dependencies are healthy"
                    if callees
                    else "it calls no other service",
                    ids,
                )
            )
        if unevaluated:
            out.append(
                _check(
                    CheckName.DOWNSTREAM_EVALUATED,
                    CheckState.FAIL,
                    f"no telemetry with known health for dependencies {unevaluated}",
                )
            )
        elif not callees:
            out.append(
                _check(
                    CheckName.DOWNSTREAM_EVALUATED,
                    CheckState.NOT_APPLICABLE,
                    "it calls no other service",
                )
            )
        else:
            out.append(
                _check(
                    CheckName.DOWNSTREAM_EVALUATED,
                    CheckState.PASS,
                    "every dependency has telemetry with known health",
                )
            )

    # Change scope.
    change = _change_before_onset(ctx, f)
    if not ctx.change_feed_ok:
        out.append(
            _check(
                CheckName.CHANGE_SCOPE,
                CheckState.NOT_EVALUABLE,
                "no change feed: changes cannot be ruled in or out",
            )
        )
    elif change is None:
        out.append(
            _check(
                CheckName.CHANGE_SCOPE,
                CheckState.NOT_APPLICABLE,
                "no change recorded on it before its onset",
            )
        )
    else:
        cid = next(i for i, c in f.changes if c is change)
        if change.instances and f.instances:
            overlap = sorted(set(change.instances) & f.instances)
            if overlap:
                out.append(
                    _check(
                        CheckName.CHANGE_SCOPE,
                        CheckState.PASS,
                        f"change on instances {overlap[:5]} that are failing",
                        [cid],
                    )
                )
            else:
                out.append(
                    _check(
                        CheckName.CHANGE_SCOPE,
                        CheckState.FAIL,
                        "the change touched instances that are not the failing ones",
                        [cid],
                    )
                )
        else:
            out.append(
                _check(
                    CheckName.CHANGE_SCOPE,
                    CheckState.PASS,
                    f"{change.kind.value} change on the same service "
                    f"{_minutes(f.onset - change.ts) if f.onset else 'before the incident'}",
                    [cid],
                )
            )

    # Independent modalities.
    mods = f.modalities()
    if len(mods) >= 2:
        out.append(
            _check(
                CheckName.MODALITY_AGREEMENT,
                CheckState.PASS,
                f"supported by {sorted(m.value for m in mods)}",
                _supporting(ctx, service, change_ok=False),
            )
        )
    else:
        contra = _contradicting(ctx, service, leaders)
        if contra:
            out.append(
                _check(
                    CheckName.MODALITY_AGREEMENT,
                    CheckState.FAIL,
                    "another modality shows this service healthy",
                    contra,
                )
            )
        else:
            out.append(
                _check(
                    CheckName.MODALITY_AGREEMENT,
                    CheckState.NOT_EVALUABLE,
                    f"only {sorted(m.value for m in mods) or 'no'} modality supports it",
                )
            )
    return out


def _ranked_co_occurring(ctx: Context, service: str) -> list[str]:
    """Services seen in the same traces, most frequent first (ties alphabetical)."""
    counts = ctx.co_occurrence.get(service, Counter())
    return [s for s, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _minutes(delta: timedelta) -> str:
    return f"{delta.total_seconds() / 60:.1f} min before its onset"


def _supporting(ctx: Context, service: str, change_ok: bool) -> list[str]:
    f = ctx.facts[service]
    ids = [a.evidence_id for a in f.anomalies]
    if f.origin_evidence and f.origin_is_anomalous:
        ids.append(f.origin_evidence)
    ids += f.signature_evidence
    ids += f.edge_change_evidence
    if change_ok:
        ids += [i for i, _ in f.changes]
    return sorted(set(ids))


def _contradicting(ctx: Context, service: str, leaders: set[str]) -> list[str]:
    f = ctx.facts[service]
    fams = f.families()
    mods = f.modalities()
    ids = [i for i, fam, mod in f.normals if fam in fams and mod not in mods]
    tol = ctx.request.tolerances
    for other, of in ctx.facts.items():
        if (
            other != service
            and other not in leaders
            and of.origin_is_anomalous
            and (of.origin_share or 0) >= tol.error_origin_lead_share
            and of.origin_evidence
        ):
            ids.append(of.origin_evidence)
    return sorted(set(ids))


# ---------------------------------------------------------------------------------------
# Mechanism, hypothesis and verdict
# ---------------------------------------------------------------------------------------

_CHANGE_MECHANISM = {
    "deployment": Mechanism.CHANGE_INDUCED_DEPLOYMENT,
    "config": Mechanism.CHANGE_INDUCED_CONFIG,
    "feature_flag": Mechanism.CHANGE_INDUCED_FEATURE_FLAG,
    "rollout": Mechanism.CHANGE_INDUCED_ROLLOUT,
    "other": Mechanism.CHANGE_INDUCED_OTHER,
}


def _is_inferred(ctx: Context, service: str) -> bool:
    return service in ctx.graph.inferred_services and service not in ctx.own_telemetry


def _mechanism(ctx: Context, service: str, checks: list[Check], rule: str | None) -> Mechanism:
    f = ctx.facts[service]
    state = {c.name: c.state for c in checks}
    fams = f.families()
    if state.get(CheckName.CHANGE_SCOPE) is CheckState.PASS:
        change = _change_before_onset(ctx, f)
        assert change is not None
        return _CHANGE_MECHANISM[change.kind.value]
    if "saturation" in fams:
        return Mechanism.SATURATION
    if _is_inferred(ctx, service):
        return Mechanism.DEPENDENCY_FAILURE if "error" in fams else Mechanism.DEPENDENCY_LATENCY
    if "error" in fams:
        return Mechanism.ERROR_THROWING
    if "latency" in fams:
        return Mechanism.LATENCY_DEGRADATION
    if any(a.family == "rate" and a.direction == "up" for a in f.anomalies):
        return Mechanism.TRAFFIC_SURGE
    if rule == "R4":
        return Mechanism.BEHAVIOUR_CHANGE
    return Mechanism.UNKNOWN


def _hypothesis(mechanism: Mechanism) -> HypothesisKind:
    if mechanism.value.startswith("change_induced"):
        return HypothesisKind.CHANGE_INDUCED
    return {
        Mechanism.SATURATION: HypothesisKind.SATURATION,
        Mechanism.DEPENDENCY_FAILURE: HypothesisKind.DEPENDENCY_FAILURE,
        Mechanism.DEPENDENCY_LATENCY: HypothesisKind.DEPENDENCY_FAILURE,
        Mechanism.TRAFFIC_SURGE: HypothesisKind.TRAFFIC_SURGE,
        Mechanism.BEHAVIOUR_CHANGE: HypothesisKind.BEHAVIOUR_CHANGE,
    }.get(mechanism, HypothesisKind.INTERNAL_FAILURE)


def _classify(
    checks: list[Check], contradicting: list[str]
) -> tuple[Classification, Support, list[str]]:
    st = {c.name: c.state for c in checks}
    failed = [c.name.value for c in checks if c.state is CheckState.FAIL]
    passes = [n for n in SUBSTANTIVE if st.get(n) is CheckState.PASS]
    downstream_ok = st.get(CheckName.DOWNSTREAM_EVALUATED) in (
        CheckState.PASS,
        CheckState.NOT_APPLICABLE,
    )
    reasons: list[str] = []
    if failed:
        reasons.append(f"failed checks: {failed}")
    if contradicting:
        reasons.append(f"{len(contradicting)} contradicting evidence item(s)")
    if not downstream_ok:
        reasons.append("a dependency on its path has not been evaluated")
    if failed or contradicting or not downstream_ok or not passes:
        if not passes:
            reasons.append("no substantive check passed")
        return Classification.INSUFFICIENT, Support.NONE, reasons
    temporal = next((c for c in checks if c.name is CheckName.TEMPORAL_PRECEDENCE), None)
    # A clock tie is expected for synchronous call chains. It is only offset when the trace
    # structure shows propagation AND a scope-matched change precedes the onset; a
    # coincidental change alone can never reach CONFIRMED (docs/rca.md, deviation D2).
    tie_offset = (
        temporal is not None
        and temporal.qualifier == "tie"
        and st.get(CheckName.PROPAGATION) is CheckState.PASS
        and st.get(CheckName.CHANGE_SCOPE) is CheckState.PASS
    )
    confirmed = (
        st.get(CheckName.MODALITY_AGREEMENT) is CheckState.PASS
        and st.get(CheckName.PROPAGATION) is CheckState.PASS
        and (
            st.get(CheckName.TEMPORAL_PRECEDENCE) in (CheckState.PASS, CheckState.NOT_APPLICABLE)
            or tie_offset
        )
        and (
            st.get(CheckName.ISOLATION) is CheckState.PASS
            or st.get(CheckName.CHANGE_SCOPE) is CheckState.PASS
        )
        and st.get(CheckName.BASELINE_VALID) is CheckState.PASS
    )
    if confirmed:
        return Classification.CONFIRMED, Support.STRONG, [f"passed: {[p.value for p in passes]}"]
    missing = [
        n.value
        for n in (CheckName.MODALITY_AGREEMENT, CheckName.PROPAGATION, CheckName.BASELINE_VALID)
        if st.get(n) is not CheckState.PASS
    ]
    reasons.append(f"passed: {[p.value for p in passes]}; not established: {missing}")
    support = Support.MODERATE if len(passes) >= 2 else Support.WEAK
    return Classification.PLAUSIBLE, support, reasons


def _next_checks(ctx: Context, service: str | None, checks: list[Check]) -> list[NextCheck]:
    out: list[NextCheck] = []
    if service is None:
        return out
    st = {c.name: c for c in checks}
    ds = st.get(CheckName.DOWNSTREAM_EVALUATED)
    if ds and ds.state in (CheckState.FAIL, CheckState.NOT_EVALUABLE):
        if ctx.topology_known(service):
            deps = sorted(c for c in ctx.graph.callees.get(service, set()) if not ctx.evaluable(c))
        else:
            deps = _ranked_co_occurring(ctx, service)
        if deps:
            out.append(
                NextCheck(
                    action=f"Check the health and error status of {', '.join(deps[:6])}",
                    reason=f"{service} may only be a symptom: its dependencies could not be evaluated",
                    services=deps[:6],
                )
            )
        else:
            out.append(
                NextCheck(
                    action=f"Find out which services {service} depends on",
                    reason="its dependencies are unknown",
                    services=[service],
                )
            )
    prop = st.get(CheckName.PROPAGATION)
    if prop and prop.state is CheckState.NOT_EVALUABLE and "known span status" in prop.reason:
        out.append(
            NextCheck(
                action=f"Inspect failing traces through {service} for the deepest failing span",
                reason="span status is missing, so error propagation could not be traced",
                services=[service],
            )
        )
    change = st.get(CheckName.CHANGE_SCOPE)
    if change and change.state is CheckState.NOT_EVALUABLE:
        out.append(
            NextCheck(
                action=f"Check what changed on {service} (deployments, configuration, feature flags) "
                "shortly before the incident",
                reason="no change feed is configured",
                services=[service],
            )
        )
    temporal = st.get(CheckName.TEMPORAL_PRECEDENCE)
    propagation_passed = prop is not None and prop.state is CheckState.PASS
    if temporal and temporal.state is CheckState.NOT_EVALUABLE and not propagation_passed:
        out.append(
            NextCheck(
                action=f"Compare the onset of {service} with its callers at finer time resolution",
                reason=temporal.reason,
                services=[service],
            )
        )
    base = st.get(CheckName.BASELINE_VALID)
    if base and base.state is CheckState.NOT_EVALUABLE and ctx.facts[service].anomalies:
        out.append(
            NextCheck(
                action=f"Compare {service} with the same time on the previous day",
                reason="no valid baseline was available",
                services=[service],
            )
        )
    return out


def decide(ctx: Context) -> tuple[list[Candidate], Verdict]:
    primary, other = _pool(ctx)
    leaders_with_rule: list[tuple[str, str]] = []
    for comp in ctx.graph.components(primary):
        leaders_with_rule += _decide_component(ctx, comp)
    leaders = {s for s, _ in leaders_with_rule}
    rule_of = dict(leaders_with_rule)

    candidates: list[Candidate] = []
    results: dict[
        str, tuple[Classification, Support, list[str], list[Check], Mechanism, list[str], list[str]]
    ] = {}

    def _rank_key(s: str) -> tuple[float, int, str, str]:
        onset = ctx.facts[s].onset
        return (
            -(ctx.facts[s].origin_share or 0.0),
            -len(ctx.facts[s].modalities()),
            onset.isoformat() if onset else "9999",
            s,
        )

    ordered = sorted(leaders) + sorted(primary - leaders, key=_rank_key)
    for rank, service in enumerate(ordered, start=1):
        f = ctx.facts[service]
        checks = _checks(ctx, service, leaders)
        contradicting = _contradicting(ctx, service, leaders)
        rule = rule_of.get(service)
        mechanism = _mechanism(ctx, service, checks, rule)
        change_ok = any(
            c.name is CheckName.CHANGE_SCOPE and c.state is CheckState.PASS for c in checks
        )
        supporting = _supporting(ctx, service, change_ok)
        for eid in supporting:
            ctx.eb.relate(eid, f"cand:{service}", RelationKind.SUPPORTS)
        for eid in contradicting:
            ctx.eb.relate(eid, f"cand:{service}", RelationKind.CONTRADICTS)
        if change_ok:
            for cid, _ in f.changes:
                for a in f.anomalies:
                    ctx.eb.relate(cid, a.evidence_id, RelationKind.PRECEDES)
        features = {
            "anomalous": f.anomalous,
            "onset": f.onset.isoformat() if f.onset else None,
            "modalities": sorted(m.value for m in f.modalities()),
            "families": sorted(f.families()),
            "error_origin_share": f.origin_share,
            "error_origin_anomalous": f.origin_is_anomalous,
            "multi_source_error_signatures": f.multi_source_error_signatures,
            "changes": len(f.changes),
            "edge_changes": len(f.edge_change_evidence),
            "anomalous_dependents": sorted(ctx.anomalous() & ctx.graph.upstream(service)),
            "anomalous_dependencies": sorted(ctx.anomalous() & ctx.graph.downstream(service)),
            "inferred_service": _is_inferred(ctx, service),
        }
        candidates.append(
            Candidate(
                service=service,
                rank=rank,
                rule=rule,
                rule_sentence=RULES.get(rule) if rule else None,
                hypothesis=_hypothesis(mechanism),
                inferred=_is_inferred(ctx, service),
                features=features,
                checks=checks,
                supporting_evidence_ids=supporting,
                contradicting_evidence_ids=contradicting,
            )
        )
        if service in leaders:
            cls, sup, reasons = _classify(checks, contradicting)
            results[service] = (cls, sup, reasons, checks, mechanism, supporting, contradicting)
            ctx.trace.append(f"leader {service} by {rule}: {cls.value} ({'; '.join(reasons)})")

    return candidates, _verdict(ctx, candidates, results, other)


def _verdict(
    ctx: Context,
    candidates: list[Candidate],
    results: dict[
        str, tuple[Classification, Support, list[str], list[Check], Mechanism, list[str], list[str]]
    ],
    other: set[str],
) -> Verdict:
    gaps = list(ctx.coverage_gaps)
    if not ctx.change_feed_ok and "No changes source configured" not in gaps:
        gaps.append("Change feed unavailable: change-induced causes cannot be ruled in or out")
    other_list = sorted(other)

    if not candidates:
        adequate = not any(
            g.startswith(
                (
                    "No metrics",
                    "No traces",
                    "No logs",
                    "metrics collection failed",
                    "traces collection failed",
                    "logs collection failed",
                    "Span status is unknown",
                )
            )
            for g in gaps
        )
        if adequate and ctx.valid_baselines > 0:
            return Verdict(
                classification=Classification.NO_ANOMALY,
                support=Support.NONE,
                display_label="Nothing found in what was checked",
                summary="No service deviated from its baseline in the collected telemetry.",
                reasons=["no anomaly, error origin, new error signature or changed call pattern"],
                coverage_gaps=gaps,
                other_anomalies=other_list,
            )
        return Verdict(
            classification=Classification.INSUFFICIENT,
            support=Support.NONE,
            display_label="Not enough evidence — next check: restore missing telemetry",
            summary="Nothing anomalous was found, but coverage is too incomplete to say so.",
            reasons=["no anomaly found with incomplete coverage"],
            next_checks=[
                NextCheck(
                    action="Restore the missing telemetry sources and re-run",
                    reason="; ".join(gaps) or "no baseline available",
                )
            ],
            coverage_gaps=gaps,
            other_anomalies=other_list,
        )

    if not results:
        top = [c.service for c in candidates[:3]]
        nc = [
            NextCheck(
                action=f"Distinguish between {', '.join(top)}: compare their failing traces and onsets",
                reason="no decision rule singled out a leading candidate",
                services=top,
            )
        ]
        for c in candidates[:1]:
            nc += _next_checks(ctx, c.service, c.checks)
        return Verdict(
            classification=Classification.INSUFFICIENT,
            support=Support.NONE,
            display_label=f"Not enough evidence — next check: {nc[0].action}",
            summary=f"Anomalies were found ({', '.join(top)}) but no candidate could be singled out.",
            reasons=["anomalies present without a leading candidate"],
            next_checks=nc,
            coverage_gaps=gaps,
            blast_radius=sorted(ctx.anomalous()),
            other_anomalies=other_list,
        )

    root_causes: list[RootCause] = []
    reasons: list[str] = []
    next_checks: list[NextCheck] = []
    classes: list[Classification] = []
    for service in sorted(results):
        cls, sup, why, checks, mechanism, supporting, contradicting = results[service]
        if cls is Classification.CONFIRMED and not ctx.change_feed_ok:
            cls, sup = Classification.PLAUSIBLE, Support.MODERATE
            why = [*why, "capped at PLAUSIBLE: no change feed"]
        if mechanism.value.startswith("change_induced") and not any(
            ctx.facts[service].changes
        ):  # defensive: never claim a change mechanism without change evidence
            mechanism = Mechanism.UNKNOWN
        classes.append(cls)
        reasons += [f"{service}: {r}" for r in why]
        next_checks += _next_checks(ctx, service, checks)
        if cls in (Classification.CONFIRMED, Classification.PLAUSIBLE):
            root_causes.append(
                RootCause(
                    service=service,
                    mechanism=mechanism,
                    support=sup,
                    supporting_evidence_ids=supporting,
                    contradicting_evidence_ids=contradicting,
                )
            )
    if root_causes and all(c is Classification.CONFIRMED for c in classes):
        classification = Classification.CONFIRMED
    elif root_causes:
        classification = Classification.PLAUSIBLE
    else:
        classification = Classification.INSUFFICIENT
    support = max((rc.support for rc in root_causes), key=SUPPORT_ORDER.index, default=Support.NONE)
    blast = (
        sorted(
            ctx.anomalous()
            & set().union(*(ctx.graph.upstream(rc.service) | {rc.service} for rc in root_causes))
        )
        if root_causes
        else sorted(ctx.anomalous())
    )

    names = ", ".join(f"{rc.service} ({rc.mechanism.value})" for rc in root_causes)
    if classification is Classification.CONFIRMED:
        label = f"Strongly supported: {names}"
        summary = f"Root cause strongly supported by independent evidence: {names}."
    elif classification is Classification.PLAUSIBLE:
        label = f"Likely cause: {names}"
        summary = f"Likely root cause: {names}. Not every distinguishing check could be completed."
    else:
        first = next_checks[0].action if next_checks else "collect more telemetry"
        label = f"Not enough evidence — next check: {first}"
        leaders = ", ".join(sorted(results))
        summary = f"Leading candidate(s) {leaders} could not be established as root cause."
    return Verdict(
        classification=classification,
        support=support,
        root_causes=root_causes,
        display_label=label,
        summary=summary,
        reasons=reasons,
        next_checks=next_checks,
        coverage_gaps=gaps,
        blast_radius=blast,
        other_anomalies=other_list,
    )
