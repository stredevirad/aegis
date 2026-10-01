"""Explicit investigation windows (never "now minus 15 minutes" inside a query).

An investigation stores its incident window and baseline window when it is created, so the
collector, the core and any later replay agree on exactly which time ranges were examined.

Rules for an incident that started at ``started_at`` and is investigated at ``now``:

* ``started_at`` later than ``now`` (sender clock ahead) is clamped to ``now``; the skew is
  reported back to the caller so it can be recorded.
* The incident window opens ``lead`` before the start (onsets precede the alert by the
  alert's ``for`` duration and the scrape interval) and closes at ``now``, capped at
  ``max_length`` after the start.
* The primary baseline is the ``baseline_length`` immediately before the incident window.
  A second baseline (same window 24 h earlier) is derived deterministically by the worker;
  the core uses the first valid one per service (ADR-008 amendment).

Collectors widen each query by the scrape interval and report the actual range they asked
for in the item provenance; clock skew between sources is handled by the core's precedence
tolerance band, not by pretending timestamps are synchronised.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from aegis.jobs.queue import Windows

LEAD = timedelta(minutes=5)
MAX_LENGTH = timedelta(minutes=60)
BASELINE_LENGTH = timedelta(minutes=60)
PREVIOUS_DAY = timedelta(days=1)


@dataclass(frozen=True)
class WindowDecision:
    windows: Windows
    clamped_future_start: bool


def for_incident(
    started_at: datetime,
    now: datetime,
    *,
    lead: timedelta = LEAD,
    max_length: timedelta = MAX_LENGTH,
    baseline_length: timedelta = BASELINE_LENGTH,
) -> WindowDecision:
    if started_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    clamped = started_at > now
    start = min(started_at, now)
    window_start = start - lead
    window_end = min(now, start + max_length)
    if window_end <= window_start:  # cannot happen with lead > 0; defensive
        window_end = window_start + lead
    return WindowDecision(
        Windows(
            incident_start=window_start,
            incident_end=window_end,
            baseline_start=window_start - baseline_length,
            baseline_end=window_start,
        ),
        clamped_future_start=clamped,
    )


def previous_day(w: Windows) -> tuple[datetime, datetime]:
    return w.incident_start - PREVIOUS_DAY, w.incident_end - PREVIOUS_DAY
