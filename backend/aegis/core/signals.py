"""Per-service signal extraction and baseline-vs-incident anomaly detection.

Signals come from metric series when the collector provided them, and are derived from
spans otherwise (request rate, error ratio and latency of a service's entry spans). Derived
rates reflect sampled traces, so only ratios and latencies are compared across windows.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aegis.core.model import (
    AnalysisRequest,
    ChangeEvent,
    MetricSeries,
    Modality,
    SignalKind,
    Span,
    SpanKind,
    SpanStatus,
    TimeWindow,
    Tolerances,
)
from aegis.core.stats import mad_sigma, median, quantile


@dataclass(frozen=True)
class Point:
    ts: datetime
    value: float
    count: float | None


@dataclass
class ServiceSignal:
    service: str
    signal: SignalKind
    origin: str  # "metrics" | "spans"
    points: list[Point]
    sources: list[dict[str, str]] = field(default_factory=list)

    @property
    def modality(self) -> Modality:
        return Modality.METRICS if self.origin == "metrics" else Modality.TRACES


@dataclass(frozen=True)
class SignalAssessment:
    service: str
    signal: SignalKind
    origin: str
    modality: Modality
    baseline_state: str  # "valid" | "invalid:<reason>"
    baseline_value: float | None
    incident_value: float | None
    threshold: float | None
    anomalous: bool
    direction: str | None  # "up" | "down" | None
    onset: datetime | None
    detail: str
    sources: tuple[tuple[str, str], ...]

    @property
    def baseline_valid(self) -> bool:
        return self.baseline_state == "valid"


def _floor(ts: datetime, bucket: timedelta) -> datetime:
    epoch = ts.timestamp()
    size = bucket.total_seconds()
    return datetime.fromtimestamp(epoch - (epoch % size), tz=ts.tzinfo)


def _merge_metric_series(series: list[MetricSeries], bucket: timedelta) -> list[Point]:
    """Combine several series of one (service, signal) into one bucketed series."""
    signal = series[0].signal
    buckets: dict[datetime, list[tuple[float, float | None]]] = defaultdict(list)
    for s in series:
        for p in s.points:
            buckets[_floor(p.ts, bucket)].append((p.value, p.count))
    points: list[Point] = []
    for ts in sorted(buckets):
        vals = buckets[ts]
        counts = [c for _, c in vals if c is not None]
        count = sum(counts) if counts else None
        if signal is SignalKind.REQUEST_RATE:
            # Several series in one bucket are different label sets (routes, status codes):
            # rates add up.
            value = sum(v for v, _ in vals)
        elif signal is SignalKind.ERROR_RATIO:
            weighted = [(v, c) for v, c in vals if c is not None and c > 0]
            if weighted:
                total = sum(c for _, c in weighted)
                value = sum(v * c for v, c in weighted) / total
            else:
                value = sum(v for v, _ in vals) / len(vals)
        else:  # latency p95 and saturation: conservative maximum
            value = max(v for v, _ in vals)
        points.append(Point(ts=ts, value=value, count=count))
    return points


def _entry_spans(spans: list[Span]) -> dict[str, list[Span]]:
    """Spans that represent a request entering a service."""
    by_id = {(s.trace_id, s.span_id): s for s in spans}
    per_service_server: dict[str, list[Span]] = defaultdict(list)
    per_service_entry: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        if s.kind is SpanKind.SERVER or s.kind is SpanKind.CONSUMER:
            per_service_server[s.service].append(s)
        parent = by_id.get((s.trace_id, s.parent_span_id)) if s.parent_span_id else None
        if parent is None or parent.service != s.service:
            per_service_entry[s.service].append(s)
    result: dict[str, list[Span]] = {}
    for service in set(per_service_server) | set(per_service_entry):
        result[service] = per_service_server.get(service) or per_service_entry[service]
    return result


def _derive_from_spans(spans: list[Span], bucket: timedelta) -> list[ServiceSignal]:
    derived: list[ServiceSignal] = []
    for service, entries in sorted(_entry_spans(spans).items()):
        by_bucket: dict[datetime, list[Span]] = defaultdict(list)
        for s in entries:
            by_bucket[_floor(s.start, bucket)].append(s)
        rate: list[Point] = []
        ratio: list[Point] = []
        p95: list[Point] = []
        for ts in sorted(by_bucket):
            group = by_bucket[ts]
            n = float(len(group))
            rate.append(Point(ts, n / bucket.total_seconds(), n))
            known = [s for s in group if s.status is not SpanStatus.UNKNOWN]
            # Only derive an error ratio when status is known for most spans; otherwise a
            # missing status would read as "healthy" (Council B: no vacuous passes).
            if known and len(known) >= 0.8 * len(group):
                ratio.append(
                    Point(ts, sum(1 for s in known if s.failed) / len(known), float(len(known)))
                )
            p95.append(Point(ts, quantile([s.duration_ms for s in group], 0.95), n))
        provenance = sorted({(s.provenance.provider, s.provenance.query or "") for s in entries})
        sources = [{"provider": p, "query": q} for p, q in provenance[:5]]
        derived.append(ServiceSignal(service, SignalKind.REQUEST_RATE, "spans", rate, sources))
        if ratio:
            derived.append(ServiceSignal(service, SignalKind.ERROR_RATIO, "spans", ratio, sources))
        derived.append(ServiceSignal(service, SignalKind.LATENCY_P95_MS, "spans", p95, sources))
    return derived


def extract_signals(
    metrics: list[MetricSeries], spans: list[Span], tolerances: Tolerances
) -> list[ServiceSignal]:
    bucket = timedelta(seconds=tolerances.bucket_seconds)
    grouped: dict[tuple[str, SignalKind], list[MetricSeries]] = defaultdict(list)
    for s in metrics:
        if s.points:
            grouped[(s.service, s.signal)].append(s)
    signals: list[ServiceSignal] = []
    for (service, signal), series in sorted(grouped.items()):
        provenance = sorted({(s.provenance.provider, s.provenance.query or "") for s in series})
        signals.append(
            ServiceSignal(
                service=service,
                signal=signal,
                origin="metrics",
                points=_merge_metric_series(series, bucket),
                sources=[{"provider": p, "query": q} for p, q in provenance[:5]],
            )
        )
    have = {(s.service, s.signal) for s in signals}
    for d in _derive_from_spans(spans, bucket):
        # Metrics win when both exist; span-derived rate is a sample rate and is only used
        # for services without metric coverage.
        if (d.service, d.signal) not in have:
            signals.append(d)
    return signals


def _choose_baseline(
    service: str,
    points: list[Point],
    request: AnalysisRequest,
    changes: list[ChangeEvent],
) -> tuple[list[Point], str]:
    """First valid baseline window for a service, trimming at changes inside the window."""
    tol = request.tolerances
    reasons: list[str] = []
    for window in request.baselines:
        end = window.end
        for c in changes:
            if c.service == service and window.start <= c.ts < end:
                end = c.ts  # behaviour after a change inside the baseline is not a baseline
        trimmed = [p for p in points if window.start <= p.ts < end]
        if end < window.end and len(trimmed) < tol.min_baseline_points:
            reasons.append("change inside baseline window")
            continue
        if len(trimmed) < tol.min_baseline_points:
            reasons.append("too few baseline points")
            continue
        return trimmed, "valid"
    return [], "invalid:" + ("; ".join(dict.fromkeys(reasons)) or "no baseline data")


def _onset(points: list[Point], exceeds: list[bool]) -> datetime | None:
    """First point that exceeds and is confirmed by 2 of the 3 points starting there."""
    for i, flag in enumerate(exceeds):
        if not flag:
            continue
        window = exceeds[i : i + 3]
        if sum(window) >= min(2, len(window)):
            return points[i].ts
    return None


def assess(
    sig: ServiceSignal,
    request: AnalysisRequest,
    changes: list[ChangeEvent],
) -> SignalAssessment | None:
    tol = request.tolerances
    incident_window: TimeWindow = request.incident
    incident = [p for p in sig.points if incident_window.contains(p.ts)]
    if not incident:
        return None
    baseline, state = _choose_baseline(sig.service, sig.points, request, changes)
    values = [p.value for p in incident]
    inc_value = quantile(values, 0.75)
    base_value: float | None = median([p.value for p in baseline]) if baseline else None
    sources = tuple(sorted((s["provider"], s.get("query", "")) for s in sig.sources))

    def result(
        anomalous: bool,
        threshold: float | None,
        direction: str | None,
        exceeds: list[bool] | None,
        detail: str,
    ) -> SignalAssessment:
        onset = _onset(incident, exceeds) if anomalous and exceeds else None
        return SignalAssessment(
            service=sig.service,
            signal=sig.signal,
            origin=sig.origin,
            modality=sig.modality,
            baseline_state=state,
            baseline_value=base_value,
            incident_value=inc_value,
            threshold=threshold,
            anomalous=anomalous,
            direction=direction,
            onset=onset,
            detail=detail,
            sources=sources,
        )

    if sig.signal is SignalKind.ERROR_RATIO:
        counts = [p.count for p in incident if p.count is not None]
        if counts and sum(counts) < tol.min_requests_for_ratio:
            return result(False, None, None, None, "too few requests to judge an error ratio")
        if baseline:
            assert base_value is not None
            spread = max(mad_sigma([p.value for p in baseline]), 0.002)
            threshold = max(
                tol.error_ratio_floor,
                base_value + max(tol.error_ratio_abs_increase, tol.robust_z * spread),
            )
            exceeds = [p.value >= threshold for p in incident]
            anomalous = inc_value >= threshold
            return result(anomalous, threshold, "up" if anomalous else None, exceeds, "vs baseline")
        threshold = tol.absolute_error_ratio_without_baseline
        exceeds = [p.value >= threshold for p in incident]
        anomalous = inc_value >= threshold
        return result(
            anomalous,
            threshold,
            "up" if anomalous else None,
            exceeds,
            "absolute threshold (no valid baseline)",
        )

    if sig.signal is SignalKind.LATENCY_P95_MS:
        if not baseline or base_value is None:
            return result(False, None, None, None, "no valid baseline: latency not judged")
        spread = max(mad_sigma([p.value for p in baseline]), 0.05 * base_value, 5.0)
        threshold = max(
            base_value * tol.latency_ratio,
            base_value + max(tol.latency_floor_ms, tol.robust_z * spread),
        )
        exceeds = [p.value >= threshold for p in incident]
        anomalous = inc_value >= threshold
        return result(anomalous, threshold, "up" if anomalous else None, exceeds, "vs baseline")

    if sig.signal is SignalKind.REQUEST_RATE:
        if sig.origin != "metrics":
            return None  # sampled trace counts are not request rates
        if not baseline or base_value is None or base_value < tol.min_rate_rps:
            return result(False, None, None, None, "no usable baseline rate")
        hi = base_value * tol.rate_change_ratio
        lo = base_value / tol.rate_change_ratio
        med = median(values)
        if med >= hi:
            exceeds = [p.value >= hi for p in incident]
            return result(True, hi, "up", exceeds, "request rate surge")
        if med <= lo:
            exceeds = [p.value <= lo for p in incident]
            return result(True, lo, "down", exceeds, "request rate drop")
        return result(False, None, None, None, "request rate within baseline range")

    # SATURATION
    peak = max(values)
    high = tol.saturation_high
    if baseline:
        base_hi = quantile([p.value for p in baseline], 0.95)
        anomalous = peak >= high and base_hi < high - 0.1
        detail = "vs baseline"
    else:
        anomalous = peak >= high
        detail = "absolute threshold (no valid baseline)"
    exceeds = [p.value >= high for p in incident]
    return result(anomalous, high, "up" if anomalous else None, exceeds, detail)
