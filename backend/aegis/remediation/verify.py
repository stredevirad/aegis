"""Post-action verification (ADR-014): did the symptoms improve after the action?

Run by the worker's maintenance loop once ``verify_after`` has passed. It re-collects the
target service's telemetry through the organization's integrations and compares two windows
of the same signals:

* **during** — the incident window of the investigation the proposal was based on (or the
  ten minutes before execution when there is none);
* **after** — from ``verify_after`` (execution + settle time) until now.

Decision (a heuristic, reported with its numbers — never just "200 OK"):

* *recovered* — the error ratio after the action is at most ``max(2 %, 25 % of during)`` with
  at least 20 requests observed, and p95 latency did not worsen by more than 50 %;
* *not_recovered* — enough data, but the criteria fail;
* *not_evaluable* — the investigation used synthetic data, no telemetry integration is usable,
  collection failed, or too little traffic was observed.

The flag's current value is read again: if someone changed it since the execution, the result
says so. Not recovered and not evaluable both leave the proposal in ``verification_failed``
so an operator looks at it; the incident is never closed automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aegis.core.model import SignalKind, TelemetrySnapshot, Tolerances
from aegis.core.signals import extract_signals

MIN_REQUESTS = 20


@dataclass(frozen=True)
class WindowStats:
    error_ratio: float | None
    p95_ms: float | None
    requests: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "error_ratio": self.error_ratio,
            "p95_ms": self.p95_ms,
            "requests": round(self.requests, 1),
        }


def window_stats(
    snapshot: TelemetrySnapshot, service: str, start: datetime, end: datetime
) -> WindowStats:
    signals = [
        s
        for s in extract_signals(snapshot.metrics, snapshot.spans, Tolerances())
        if s.service == service
    ]
    err_num = err_den = 0.0
    p95s: list[float] = []
    requests = 0.0
    for sig in signals:
        pts = [p for p in sig.points if start <= p.ts < end]
        if sig.signal is SignalKind.ERROR_RATIO:
            for p in pts:
                w = p.count if p.count else 1.0
                err_num += p.value * w
                err_den += w
        elif sig.signal is SignalKind.LATENCY_P95_MS:
            p95s += [p.value for p in pts]
        elif sig.signal is SignalKind.REQUEST_RATE:
            requests += sum(p.count or 0.0 for p in pts)
    if not requests:
        requests = err_den
    return WindowStats(
        err_num / err_den if err_den else None, max(p95s) if p95s else None, requests
    )


def decide(during: WindowStats, after: WindowStats) -> tuple[str, str]:
    if after.error_ratio is None or after.requests < MIN_REQUESTS:
        return (
            "not_evaluable",
            f"too little traffic after the action ({after.requests:.0f} requests)",
        )
    if during.error_ratio is None:
        return "not_evaluable", "no error-ratio data for the incident window"
    limit = max(0.02, during.error_ratio * 0.25)
    if after.error_ratio > limit:
        return (
            "not_recovered",
            f"error ratio {after.error_ratio:.3f} after vs {during.error_ratio:.3f} during (limit {limit:.3f})",
        )
    if during.p95_ms and after.p95_ms and after.p95_ms > during.p95_ms * 1.5:
        return (
            "not_recovered",
            f"p95 latency rose to {after.p95_ms:.0f} ms from {during.p95_ms:.0f} ms",
        )
    return (
        "recovered",
        f"error ratio {after.error_ratio:.3f} after vs {during.error_ratio:.3f} during",
    )


def windows_for(
    executed_at: datetime, verify_after: datetime, incident_window: tuple[datetime, datetime] | None
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    during = incident_window or (executed_at - timedelta(minutes=10), executed_at)
    after = (verify_after, max(datetime.now(UTC), verify_after + timedelta(seconds=30)))
    return during, after
