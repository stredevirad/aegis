"""Log signature counting.

Log bodies are untrusted text. They are reduced to templates (variable parts masked) and
*counted*; their meaning is never interpreted by this code. A "new" signature requires a
minimum count from more than one source instance, so a single compromised emitter cannot
manufacture a new error pattern on its own (Council B).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from aegis.core.model import AnalysisRequest, LogRecord, Severity, TimeWindow

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MASKS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<uuid>",
    ),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b(?:0x)?[0-9a-fA-F]{12,}\b"), "<hex>"),
    (re.compile(r'"[^"]{0,200}"'), '"<str>"'),
    (re.compile(r"'[^']{0,200}'"), "'<str>'"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|us|ns|%|kb|mb|gb)?\b", re.IGNORECASE), "<n>"),
]
TEMPLATE_MAX = 240


def template_of(body: str) -> str:
    text = _CONTROL.sub(" ", body)[:1024]
    for pattern, repl in _MASKS:
        text = pattern.sub(repl, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:TEMPLATE_MAX] or "<empty>"


def is_error(record: LogRecord) -> bool:
    if record.severity in (Severity.ERROR, Severity.FATAL):
        return True
    return record.http_status is not None and record.http_status >= 500


@dataclass
class SignatureStats:
    service: str
    template: str
    error: bool
    baseline_count: int = 0
    incident_count: int = 0
    incident_sources: set[str] = field(default_factory=set)
    trace_ids: set[str] = field(default_factory=set)
    first_seen: datetime | None = None
    providers: set[tuple[str, str]] = field(default_factory=set)


@dataclass(frozen=True)
class SignatureFinding:
    stats: SignatureStats
    status: str  # "new" | "increased" | "present"
    single_source: bool
    baseline_evaluable: bool


@dataclass(frozen=True)
class ErrorLogRate:
    service: str
    baseline_per_min: float | None
    incident_per_min: float
    incident_count: int
    anomalous: bool
    onset: datetime | None


def _baseline_window(service_logs: list[LogRecord], request: AnalysisRequest) -> TimeWindow | None:
    for w in request.baselines:
        if any(w.contains(r.ts) for r in service_logs):
            return w
    return None


def analyze_logs(
    logs: list[LogRecord], request: AnalysisRequest
) -> tuple[list[SignatureFinding], list[ErrorLogRate]]:
    tol = request.tolerances
    incident = request.incident
    by_service: dict[str, list[LogRecord]] = defaultdict(list)
    for r in logs:
        by_service[r.service].append(r)

    findings: list[SignatureFinding] = []
    rates: list[ErrorLogRate] = []
    for service in sorted(by_service):
        records = by_service[service]
        base_w = _baseline_window(records, request)
        sigs: dict[tuple[str, bool], SignatureStats] = {}
        for r in sorted(records, key=lambda x: x.ts):
            err = is_error(r)
            if r.severity not in (Severity.WARN, Severity.ERROR, Severity.FATAL) and not err:
                continue
            key = (template_of(r.body), err)
            st = sigs.setdefault(key, SignatureStats(service, key[0], err))
            if base_w is not None and base_w.contains(r.ts):
                st.baseline_count += 1
            elif incident.contains(r.ts):
                st.incident_count += 1
                st.incident_sources.add(r.instance or "<unknown-instance>")
                if r.trace_id and len(st.trace_ids) < 20:
                    st.trace_ids.add(r.trace_id)
                if st.first_seen is None:
                    st.first_seen = r.ts
                st.providers.add((r.provenance.provider, r.provenance.query or ""))
        inc_minutes = incident.duration.total_seconds() / 60.0
        base_minutes = base_w.duration.total_seconds() / 60.0 if base_w else None
        for st in sorted(sigs.values(), key=lambda s: (-s.incident_count, s.template)):
            if st.incident_count < tol.min_signature_count:
                continue
            single = len(st.incident_sources) < tol.min_signature_sources
            if base_w is None or base_minutes is None:
                status = "present"
            elif st.baseline_count == 0:
                status = "new"
            elif st.incident_count / inc_minutes >= 3 * st.baseline_count / base_minutes:
                status = "increased"
            else:
                status = "present"
            findings.append(SignatureFinding(st, status, single, base_w is not None))

        err_inc = sorted(r.ts for r in records if is_error(r) and incident.contains(r.ts))
        err_base = (
            sum(1 for r in records if is_error(r) and base_w.contains(r.ts)) if base_w else None
        )
        inc_rate = len(err_inc) / inc_minutes
        base_rate = err_base / base_minutes if err_base is not None and base_minutes else None
        anomalous = len(err_inc) >= tol.min_signature_count and (
            inc_rate >= max(3 * base_rate, base_rate + 1.0) if base_rate is not None else False
        )
        onset = _elevated_onset(err_inc, request, base_rate or 0.0) if anomalous else None
        rates.append(
            ErrorLogRate(
                service=service,
                baseline_per_min=base_rate,
                incident_per_min=inc_rate,
                incident_count=len(err_inc),
                anomalous=anomalous,
                onset=onset,
            )
        )
    return findings, rates


def _elevated_onset(
    times: list[datetime], request: AnalysisRequest, base_per_min: float
) -> datetime | None:
    """First bucket whose error-log rate exceeds max(3 x baseline, baseline + 1/min),
    confirmed by 2 of the 3 buckets starting there (background errors are not an onset)."""
    size = request.tolerances.bucket_seconds
    per_bucket_threshold = max(3 * base_per_min, base_per_min + 1.0) * size / 60.0
    start = request.incident.start.timestamp()
    end = request.incident.end.timestamp()
    counts: dict[int, int] = defaultdict(int)
    for t in times:
        counts[int((t.timestamp() - start) // size)] += 1
    n_buckets = int((end - start) // size) + 1
    flags = [counts.get(i, 0) >= max(per_bucket_threshold, 1) for i in range(n_buckets)]
    for i, flag in enumerate(flags):
        window = flags[i : i + 3]
        if flag and sum(window) >= min(2, len(window)):
            return datetime.fromtimestamp(start + i * size, tz=request.incident.start.tzinfo)
    return None
