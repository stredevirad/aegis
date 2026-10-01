"""Tiny hand-built snapshots for focused core tests (synthetic, labelled as such)."""

from __future__ import annotations

from datetime import datetime, timedelta

from aegis.core.model import (
    AnalysisRequest,
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
)

T0 = datetime.fromisoformat("2026-03-02T12:00:00+00:00")
PROV = Provenance(provider="test", query="q", collected_at=T0 + timedelta(hours=1))


def window(start_min: float, end_min: float) -> TimeWindow:
    return TimeWindow(start=T0 + timedelta(minutes=start_min), end=T0 + timedelta(minutes=end_min))


def request(incident=(0, 20), baseline=(-60, 0), focus=("frontend",)) -> AnalysisRequest:
    return AnalysisRequest(
        incident=window(*incident), baselines=[window(*baseline)], focus_services=list(focus)
    )


def ratio_series(service: str, values_by_minute: dict[tuple[int, int], float], count: float = 600):
    """Error-ratio series with one point per 30s; values_by_minute maps (from,to) -> value."""
    points = []
    for half in range(-120, 40):
        minute = half / 2
        value = 0.001
        for (lo, hi), v in values_by_minute.items():
            if lo <= minute < hi:
                value = v
        points.append(MetricPoint(ts=T0 + timedelta(seconds=30 * half), value=value, count=count))
    return MetricSeries(
        service=service, signal=SignalKind.ERROR_RATIO, points=points, provenance=PROV
    )


def outcomes(*, metrics=True, logs=True, traces=True, changes=True) -> list[CollectionOutcome]:
    def o(m: Modality, on: bool) -> CollectionOutcome:
        return CollectionOutcome(
            modality=m,
            provider="test",
            status=CollectionStatus.OK if on else CollectionStatus.NOT_CONFIGURED,
        )

    return [
        o(Modality.METRICS, metrics),
        o(Modality.LOGS, logs),
        o(Modality.TRACES, traces),
        o(Modality.CHANGES, changes),
    ]


def snapshot(**kw) -> TelemetrySnapshot:
    base = dict(
        captured_at=T0 + timedelta(hours=1),
        collected_window=window(-60, 20),
        synthetic=True,
        collection=outcomes(),
    )
    base.update(kw)
    return TelemetrySnapshot(**base)


def failing_trace(
    trace_id: str, at_min: float, chain: list[str], failing_from: int, status_known=True
) -> list[Span]:
    """A linear call chain whose deepest failing span is ``chain[failing_from]``."""
    spans: list[Span] = []
    parent = None
    for i, svc in enumerate(chain):
        # the origin (index ``failing_from``) and every caller above it fail; callees below
        # the origin are healthy. ``failing_from=-1`` makes a healthy trace.
        failed = 0 <= i <= failing_from
        status = (
            (SpanStatus.ERROR if failed else SpanStatus.UNSET)
            if status_known
            else SpanStatus.UNKNOWN
        )
        sid = f"{trace_id[:8]}{i:08x}"
        spans.append(
            Span(
                trace_id=trace_id,
                span_id=sid,
                parent_span_id=parent,
                service=svc,
                operation=f"op {svc}",
                kind=SpanKind.SERVER,
                start=T0 + timedelta(minutes=at_min, milliseconds=i),
                duration_ms=10.0,
                status=status,
                provenance=PROV,
            )
        )
        parent = sid
    return spans


def log(
    service: str, at_min: float, body: str, severity=Severity.ERROR, instance="i-0"
) -> LogRecord:
    return LogRecord(
        ts=T0 + timedelta(minutes=at_min),
        service=service,
        severity=severity,
        body=body,
        instance=instance,
        provenance=PROV,
    )
