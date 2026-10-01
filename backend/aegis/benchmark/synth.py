"""Deterministic synthetic telemetry generator for benchmark scenarios.

A scenario describes a service graph, traffic, injected faults, change events, telemetry
availability, decoys and hostile log injections. The generator simulates requests through
the graph and emits metrics, sampled traces, logs and change events as a
``TelemetrySnapshot`` with ``synthetic=True``. Ground truth comes from the injected fault,
not from labels written after looking at pipeline output.

All randomness is drawn from ``random.Random(seed)``: the same scenario always produces the
same snapshot. (This module is not part of the pure core; it only produces core inputs.)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from aegis.core.model import (
    AnalysisRequest,
    ChangeEvent,
    ChangeKind,
    CollectionOutcome,
    CollectionStatus,
    LogRecord,
    MetricPoint,
    MetricSeries,
    Modality,
    Provenance,
    Severity,
    SignalKind,
    Span,
    SpanKind,
    SpanStatus,
    TelemetrySnapshot,
    TimeWindow,
    Tolerances,
    TopologyEdge,
)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServiceSpec(_Spec):
    name: str
    calls: list[str] = Field(default_factory=list)
    kind: Literal["service", "database", "cache", "external"] = "service"
    instrumented: bool = True
    base_latency_ms: float = 15.0
    error_rate: float = 0.001
    instances: int = 2
    # callee -> alternative callee used when the callee is disabled (masked fallback)
    fallback: dict[str, str] = Field(default_factory=dict)
    saturation: float | None = None  # baseline utilisation if the service reports it
    own_rps: float = 0.0  # independent traffic (e.g. a batch job not called by the entry)


class FaultSpec(_Spec):
    service: str
    type: Literal["errors", "latency", "saturation", "disable", "traffic"]
    start_offset_min: float = 2.0
    end_offset_min: float | None = None
    error_rate: float = 0.5
    added_latency_ms: float = 400.0
    saturation: float = 0.97
    traffic_multiplier: float = 4.0
    log_template: str | None = None
    instances: list[int] | None = None  # affected instance indexes (None = all)


class ChangeSpec(_Spec):
    service: str | None
    kind: ChangeKind
    offset_min: float
    summary: str
    scope: dict[str, str] = Field(default_factory=dict)
    instances: list[int] = Field(default_factory=list)


class InjectionSpec(_Spec):
    service: str
    offset_min: float = 3.0
    body: str
    count: int = 20
    severity: Severity = Severity.ERROR
    instance: str | None = "attacker-0"
    service_name_override: str | None = None


class TelemetrySpec(_Spec):
    metrics: bool = True
    logs: bool = True
    traces: bool = True
    span_status: bool = True
    parent_links: bool = True
    change_feed: bool = True
    metrics_status: CollectionStatus = CollectionStatus.OK
    logs_status: CollectionStatus = CollectionStatus.OK
    traces_status: CollectionStatus = CollectionStatus.OK
    configured_edges: bool = False
    metrics_services: list[str] | None = None  # emit metrics only for these services


class ExpectedSpec(_Spec):
    classifications: list[str]
    root_services: list[str] = Field(default_factory=list)
    mechanisms: list[str] = Field(default_factory=list)
    forbidden_root_services: list[str] = Field(default_factory=list)
    must_mention: list[str] = Field(default_factory=list)  # in candidates or next checks
    no_anomaly_allowed: bool = False


class ScenarioSpec(_Spec):
    synthetic: Literal[True] = True
    id: str
    title: str
    category: str
    description: str
    seed: int = 7
    t0: AwareDatetime = datetime.fromisoformat("2026-03-02T12:00:00+00:00")
    baseline_minutes: int = 60
    incident_minutes: int = 20
    bucket_seconds: int = 30
    entry: str
    rps: float = 20.0
    traces_per_bucket: int = 6
    services: list[ServiceSpec]
    faults: list[FaultSpec] = Field(default_factory=list)
    changes: list[ChangeSpec] = Field(default_factory=list)
    injections: list[InjectionSpec] = Field(default_factory=list)
    decoys: list[FaultSpec] = Field(default_factory=list)
    telemetry: TelemetrySpec = Field(default_factory=TelemetrySpec)
    focus: list[str] = Field(default_factory=list)
    expected: ExpectedSpec


@dataclass
class _State:
    own_error: dict[str, float]
    added_latency: dict[str, float]
    saturation: dict[str, float]
    disabled: set[str]
    traffic: float


def _active(f: FaultSpec, minute: float) -> bool:
    return minute >= f.start_offset_min and (f.end_offset_min is None or minute < f.end_offset_min)


class Generator:
    def __init__(self, spec: ScenarioSpec) -> None:
        self.spec = spec
        self.rng = random.Random(spec.seed)
        self.services = {s.name: s for s in spec.services}
        self.t0 = spec.t0
        self.start = self.t0 - timedelta(minutes=spec.baseline_minutes)
        self.end = self.t0 + timedelta(minutes=spec.incident_minutes)
        self.bucket = timedelta(seconds=spec.bucket_seconds)
        self.collected = self.end
        self._span_seq = 0

    # -- provenance ---------------------------------------------------------------------
    def _prov(self, provider: str, query: str) -> Provenance:
        return Provenance(provider=provider, query=query, collected_at=self.collected)

    # -- fault state ----------------------------------------------------------------------
    def _state(self, ts: datetime) -> _State:
        minute = (ts - self.t0).total_seconds() / 60.0
        own = {n: s.error_rate for n, s in self.services.items()}
        lat = {n: 0.0 for n in self.services}
        sat = {n: (s.saturation or 0.0) for n, s in self.services.items()}
        disabled: set[str] = set()
        traffic = 1.0
        for f in [*self.spec.faults, *self.spec.decoys]:
            if not _active(f, minute):
                continue
            if f.type == "errors":
                own[f.service] = max(own[f.service], f.error_rate)
            elif f.type == "latency":
                lat[f.service] += f.added_latency_ms
            elif f.type == "saturation":
                sat[f.service] = f.saturation
                lat[f.service] += f.added_latency_ms * 0.5
            elif f.type == "disable":
                disabled.add(f.service)
            elif f.type == "traffic":
                traffic *= f.traffic_multiplier
        return _State(own, lat, sat, disabled, traffic)

    def _callees(self, name: str, st: _State) -> list[str]:
        svc = self.services[name]
        out: list[str] = []
        for c in svc.calls:
            if c in st.disabled and c in svc.fallback:
                out.append(svc.fallback[c])
            elif c in st.disabled:
                continue
            else:
                out.append(c)
        return out

    def _fail_prob(self, name: str, st: _State, depth: int = 0) -> float:
        if depth > 20:
            return 0.0
        ok = 1.0 - st.own_error[name]
        for c in self._callees(name, st):
            ok *= 1.0 - self._fail_prob(c, st, depth + 1)
        return 1.0 - ok

    def _latency(self, name: str, st: _State, depth: int = 0) -> float:
        if depth > 20:
            return 0.0
        svc = self.services[name]
        extra = st.added_latency[name]
        if st.saturation[name] >= 0.9:
            extra += svc.base_latency_ms * 2
        return (
            svc.base_latency_ms
            + extra
            + sum(self._latency(c, st, depth + 1) for c in self._callees(name, st))
        )

    def _request_counts(self, st: _State) -> dict[str, float]:
        counts = {n: 0.0 for n in self.services}

        def walk(name: str, n: float, depth: int) -> None:
            if depth > 20:
                return
            counts[name] += n
            for c in self._callees(name, st):
                walk(c, n, depth + 1)

        walk(self.spec.entry, self.spec.rps * st.traffic * self.bucket.total_seconds(), 0)
        for name, svc in self.services.items():
            if svc.own_rps > 0:
                walk(name, svc.own_rps * self.bucket.total_seconds(), 0)
        return counts

    # -- generation -----------------------------------------------------------------------
    def snapshot(self) -> TelemetrySnapshot:
        tel = self.spec.telemetry
        metrics: list[MetricSeries] = []
        spans: list[Span] = []
        logs: list[LogRecord] = []
        series: dict[tuple[str, SignalKind], list[MetricPoint]] = {}
        ts = self.start
        while ts < self.end:
            st = self._state(ts)
            counts = self._request_counts(st)
            for name, svc in self.services.items():
                if not svc.instrumented:
                    continue
                n = counts[name]
                if n <= 0:
                    continue
                p = self._fail_prob(name, st)
                failures = sum(1 for _ in range(int(n)) if self.rng.random() < p)
                lat = self._latency(name, st) * (1.35 + self.rng.gauss(0, 0.04))
                series.setdefault((name, SignalKind.REQUEST_RATE), []).append(
                    MetricPoint(ts=ts, value=n / self.bucket.total_seconds(), count=n)
                )
                series.setdefault((name, SignalKind.ERROR_RATIO), []).append(
                    MetricPoint(ts=ts, value=failures / max(n, 1), count=n)
                )
                series.setdefault((name, SignalKind.LATENCY_P95_MS), []).append(
                    MetricPoint(ts=ts, value=max(lat, 0.1), count=n)
                )
                if svc.saturation is not None:
                    series.setdefault((name, SignalKind.SATURATION), []).append(
                        MetricPoint(
                            ts=ts,
                            value=min(1.0, max(0.0, st.saturation[name] + self.rng.gauss(0, 0.02))),
                        )
                    )
                if tel.logs:
                    logs += self._logs_for(name, ts, failures, st)
            if tel.traces:
                for k in range(self.spec.traces_per_bucket):
                    start = ts + self.bucket * (k / self.spec.traces_per_bucket)
                    spans += self._trace(start, st)
                    if k % 3 == 0:
                        spans += self._independent_traces(start, st)
            ts += self.bucket
        if tel.metrics:
            for (svc_name, signal), points in sorted(series.items()):
                if tel.metrics_services is not None and svc_name not in tel.metrics_services:
                    continue
                metrics.append(
                    MetricSeries(
                        service=svc_name,
                        signal=signal,
                        points=points,
                        provenance=self._prov(
                            "synthetic-metrics", f"{signal.value}{{service={svc_name}}}"
                        ),
                    )
                )
        logs += self._injections()
        changes = self._changes() if tel.change_feed else []
        edges = (
            [TopologyEdge(source=s.name, target=c) for s in self.spec.services for c in s.calls]
            if tel.configured_edges
            else []
        )
        return TelemetrySnapshot(
            captured_at=self.collected,
            collected_window=TimeWindow(start=self.start, end=self.end),
            environment="synthetic",
            synthetic=True,
            description=f"synthetic scenario {self.spec.id}: {self.spec.title}",
            metrics=metrics,
            logs=sorted(logs, key=lambda r: (r.ts, r.service, r.body)),
            spans=spans,
            changes=changes,
            edges=edges,
            collection=self._collection(len(metrics), len(logs), len(spans), len(changes)),
        )

    def _collection(self, m: int, lg: int, sp: int, ch: int) -> list[CollectionOutcome]:
        tel = self.spec.telemetry
        out = [
            CollectionOutcome(
                modality=Modality.METRICS,
                provider="synthetic-metrics",
                status=tel.metrics_status if tel.metrics else CollectionStatus.NOT_CONFIGURED,
                items=m,
            ),
            CollectionOutcome(
                modality=Modality.LOGS,
                provider="synthetic-logs",
                status=tel.logs_status if tel.logs else CollectionStatus.NOT_CONFIGURED,
                items=lg,
            ),
            CollectionOutcome(
                modality=Modality.TRACES,
                provider="synthetic-traces",
                status=tel.traces_status if tel.traces else CollectionStatus.NOT_CONFIGURED,
                items=sp,
            ),
        ]
        if tel.change_feed:
            out.append(
                CollectionOutcome(
                    modality=Modality.CHANGES,
                    provider="synthetic-changes",
                    status=CollectionStatus.OK,
                    items=ch,
                )
            )
        return out

    def _span_id(self) -> str:
        self._span_seq += 1
        return f"{self._span_seq:016x}"

    def _trace(self, start: datetime, st: _State) -> list[Span]:
        tel = self.spec.telemetry
        trace_id = f"{self.rng.getrandbits(128):032x}"
        out: list[Span] = []

        def status(failed: bool) -> SpanStatus:
            if not tel.span_status:
                return SpanStatus.UNKNOWN
            return SpanStatus.ERROR if failed else SpanStatus.UNSET

        def visit(name: str, parent: str | None, at: datetime, depth: int) -> tuple[bool, float]:
            svc = self.services[name]
            own_fail = self.rng.random() < st.own_error[name]
            span_id = self._span_id()
            elapsed = (
                svc.base_latency_ms * (1 + abs(self.rng.gauss(0, 0.1))) + st.added_latency[name]
            )
            child_failed = False
            for c in self._callees(name, st) if depth < 20 else []:
                callee = self.services[c]
                child_at = at + timedelta(milliseconds=elapsed)
                if callee.instrumented:
                    failed, dur = visit(c, span_id, child_at, depth + 1)
                else:
                    failed = self.rng.random() < st.own_error[c]
                    dur = (
                        callee.base_latency_ms * (1 + abs(self.rng.gauss(0, 0.1)))
                        + st.added_latency[c]
                    )
                    out.append(
                        Span(
                            trace_id=trace_id,
                            span_id=self._span_id(),
                            parent_span_id=span_id if tel.parent_links else None,
                            service=name,
                            operation=f"{callee.kind} {c}",
                            kind=SpanKind.CLIENT,
                            start=child_at,
                            duration_ms=dur,
                            status=status(failed),
                            peer_service=c,
                            provenance=self._prov("synthetic-traces", f"service={name}"),
                        )
                    )
                elapsed += dur
                child_failed = child_failed or failed
            failed = own_fail or child_failed
            out.append(
                Span(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent if tel.parent_links else None,
                    service=name,
                    operation=f"handle {name}",
                    kind=SpanKind.SERVER,
                    start=at,
                    duration_ms=elapsed,
                    status=status(failed),
                    http_status=500 if failed else 200,
                    provenance=self._prov("synthetic-traces", f"service={name}"),
                )
            )
            return failed, elapsed

        visit(self.spec.entry, None, start, 0)
        return out

    def _independent_traces(self, start: datetime, st: _State) -> list[Span]:
        out: list[Span] = []
        for name, svc in sorted(self.services.items()):
            if svc.own_rps > 0 and svc.instrumented:
                entry = self.spec.entry
                self.spec = self.spec.model_copy(update={"entry": name})
                try:
                    out += self._trace(start, st)
                finally:
                    self.spec = self.spec.model_copy(update={"entry": entry})
        return out

    def _fault_template(self, name: str, st: _State) -> str | None:
        for f in self.spec.faults:
            if f.service == name and f.log_template and st.own_error[name] >= f.error_rate * 0.99:
                return f.log_template
        return None

    def _logs_for(self, name: str, ts: datetime, failures: int, st: _State) -> list[LogRecord]:
        svc = self.services[name]
        out: list[LogRecord] = []
        prov = self._prov("synthetic-logs", f'{{service="{name}"}}')
        template = self._fault_template(name, st)
        callees = [c for c in self._callees(name, st) if self._fail_prob(c, st) > 0.05]
        for i in range(min(failures, 25)):
            instance = f"{name}-{i % max(svc.instances, 1)}"
            if template and self.rng.random() < 0.8:
                body = template.replace("{n}", str(self.rng.randint(1, 9999)))
            elif callees:
                body = f"request failed: upstream {callees[0]} returned status 500 after {self.rng.randint(5, 900)}ms"
            else:
                body = f"unhandled error processing request id={self.rng.getrandbits(48):x}"
            out.append(
                LogRecord(
                    ts=ts
                    + timedelta(seconds=self.rng.uniform(0, self.bucket.total_seconds() - 0.001)),
                    service=name,
                    severity=Severity.ERROR,
                    body=body,
                    instance=instance,
                    provenance=prov,
                )
            )
        if self.rng.random() < 0.15:  # background warnings that exist in every window
            out.append(
                LogRecord(
                    ts=ts,
                    service=name,
                    severity=Severity.WARN,
                    body=f"slow operation took {self.rng.randint(100, 400)}ms",
                    instance=f"{name}-0",
                    provenance=prov,
                )
            )
        return out

    def _injections(self) -> list[LogRecord]:
        out: list[LogRecord] = []
        for inj in self.spec.injections:
            base = self.t0 + timedelta(minutes=inj.offset_min)
            for i in range(inj.count):
                out.append(
                    LogRecord(
                        ts=base + timedelta(seconds=i * 7),
                        service=inj.service_name_override or inj.service,
                        severity=inj.severity,
                        body=inj.body[:2000],
                        instance=inj.instance,
                        provenance=self._prov("synthetic-logs", f'{{service="{inj.service}"}}'),
                    )
                )
        return out

    def _changes(self) -> list[ChangeEvent]:
        out: list[ChangeEvent] = []
        for i, c in enumerate(self.spec.changes):
            instances = [f"{c.service}-{k}" for k in c.instances] if c.service else []
            out.append(
                ChangeEvent(
                    source_id=f"chg-{self.spec.id}-{i}",
                    ts=self.t0 + timedelta(minutes=c.offset_min),
                    service=c.service,
                    kind=c.kind,
                    summary=c.summary,
                    environment="synthetic",
                    scope=c.scope,
                    instances=instances,
                    provenance=self._prov("synthetic-changes", "changes"),
                )
            )
        return out


def build_request(spec: ScenarioSpec, tolerances: Tolerances | None = None) -> AnalysisRequest:
    return AnalysisRequest(
        incident=TimeWindow(start=spec.t0, end=spec.t0 + timedelta(minutes=spec.incident_minutes)),
        baselines=[
            TimeWindow(start=spec.t0 - timedelta(minutes=spec.baseline_minutes), end=spec.t0)
        ],
        focus_services=spec.focus or [spec.entry],
        declared_incident=True,
        tolerances=tolerances or Tolerances(bucket_seconds=spec.bucket_seconds),
    )


def generate(spec: ScenarioSpec) -> tuple[TelemetrySnapshot, AnalysisRequest]:
    return Generator(spec).snapshot(), build_request(spec)


def expected_failure_probability(spec: ScenarioSpec, minute: float) -> float:
    """Analytical failure probability of the entry service at a given minute (for tests)."""
    g = Generator(spec)
    st = g._state(spec.t0 + timedelta(minutes=minute))
    return g._fail_prob(spec.entry, st)
