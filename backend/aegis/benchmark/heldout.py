"""Evaluate the product RCA core on the frozen held-out set (real OTel Demo captures).

The held-out cases were selected and their expected answers written and hashed before any
pipeline code existed (eval/heldout/). This module converts the research evidence rows into
``TelemetrySnapshot`` objects without inventing information the research engine did not
capture: span status and parent links are UNKNOWN/absent, there is no change feed and no
baseline window. Nothing in the pipeline may be tuned against these results.

Usage (from backend/):
    python -m aegis.benchmark.heldout --root .. --out ../eval/results/heldout.json
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aegis.benchmark.evaluate import Expectation, Score, score
from aegis.core import analyze
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
    SpanStatus,
    TelemetrySnapshot,
    TimeWindow,
)
from aegis.core.result import Analysis

_ACCESS_LOG_STATUS = re.compile(r'"[A-Z]+ \S+ HTTP/[\d.]+" (\d{3})')


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _severity(level: str) -> Severity:
    level = (level or "").upper()
    for s in Severity:
        if level.startswith(s.value):
            return s
    return {"WARNING": Severity.WARN, "CRITICAL": Severity.FATAL}.get(level, Severity.UNKNOWN)


def _metrics(rows: list[dict[str, Any]], collected: datetime) -> list[MetricSeries]:
    """Counter series (request counts by status code) -> request rate and error ratio."""
    totals: dict[str, dict[float, float]] = defaultdict(lambda: defaultdict(float))
    errors: dict[str, dict[float, float]] = defaultdict(lambda: defaultdict(float))
    queries: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        data = json.loads(row["data"])
        labels = data.get("metric", {})
        service = labels.get("service_name") or row.get("service_resource")
        values = data.get("values", [])
        if not service or len(values) < 2:
            continue
        status = str(labels.get("http_response_status_code", ""))
        queries[service].add(str(data.get("query", ""))[:500])
        prev: float | None = None
        for ts, raw in values:
            v = float(raw)
            if prev is not None:
                inc = v - prev if v >= prev else v  # counter reset
                totals[service][float(ts)] += inc
                if status.startswith("5"):
                    errors[service][float(ts)] += inc
            prev = v
    out: list[MetricSeries] = []
    for service in sorted(totals):
        stamps = sorted(totals[service])
        if len(stamps) < 2:
            continue
        step = max(stamps[1] - stamps[0], 1.0)
        prov = Provenance(
            provider="prometheus (research capture)",
            query="; ".join(sorted(queries[service]))[:2000],
            collected_at=collected,
        )
        rate = [
            MetricPoint(
                ts=datetime.fromtimestamp(t, tz=collected.tzinfo),
                value=totals[service][t] / step,
                count=totals[service][t],
            )
            for t in stamps
        ]
        ratio = [
            MetricPoint(
                ts=datetime.fromtimestamp(t, tz=collected.tzinfo),
                value=(errors[service][t] / totals[service][t]) if totals[service][t] > 0 else 0.0,
                count=totals[service][t],
            )
            for t in stamps
        ]
        out.append(
            MetricSeries(
                service=service, signal=SignalKind.REQUEST_RATE, points=rate, provenance=prov
            )
        )
        out.append(
            MetricSeries(
                service=service, signal=SignalKind.ERROR_RATIO, points=ratio, provenance=prov
            )
        )
    return out


def _logs(rows: list[dict[str, Any]], collected: datetime) -> list[LogRecord]:
    out: list[LogRecord] = []
    for row in rows:
        data = json.loads(row["data"])
        message = str(data.get("message", ""))[:2048]
        m = _ACCESS_LOG_STATUS.search(message)
        out.append(
            LogRecord(
                ts=_dt(row["observed_at"]),
                service=row.get("service_resource") or "unknown",
                severity=_severity(str(data.get("level", ""))),
                body=message,
                http_status=int(m.group(1)) if m else None,
                provenance=Provenance(
                    provider="opensearch (research capture)",
                    query=str(data.get("index", ""))[:200],
                    collected_at=collected,
                ),
            )
        )
    return out


def _spans(rows: list[dict[str, Any]], collected: datetime) -> list[Span]:
    out: list[Span] = []
    for row in rows:
        data = json.loads(row["data"])
        trace_id = str(data.get("trace_id", ""))[:64]
        start = _dt(row["observed_at"])
        for sp in data.get("spans", []):
            out.append(
                Span(
                    trace_id=trace_id or "unknown",
                    span_id=str(sp.get("span_id") or "x")[:32],
                    parent_span_id=None,  # not captured by the research adapter
                    service=str(sp.get("service") or "unknown")[:200],
                    operation=str(sp.get("operation") or "unknown")[:200],
                    start=start,
                    duration_ms=float(sp.get("duration_ms") or 0.0),
                    status=SpanStatus.UNKNOWN,  # discarded by the research adapter (AI-04)
                    provenance=Provenance(
                        provider="jaeger (research capture)",
                        query=f"service={row.get('service_resource')}",
                        collected_at=collected,
                    ),
                )
            )
    return out


def build_case(
    case: dict[str, Any], evidence_rows: list[dict[str, Any]]
) -> tuple[TelemetrySnapshot, AnalysisRequest]:
    start = _dt(case["incident_start_time"])
    collected = start
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in evidence_rows:
        by_type[r["type"]].append(r)
    metrics = _metrics(by_type["METRIC"], collected)
    logs = _logs(by_type["LOG"], collected)
    spans = _spans(by_type["TRACE"], collected)

    def outcome(m: Modality, items: int, provider: str, missing_detail: str) -> CollectionOutcome:
        status = CollectionStatus.OK if items else CollectionStatus.PARTIAL
        return CollectionOutcome(
            modality=m,
            provider=provider,
            status=status,
            items=items,
            detail="" if items else missing_detail,
        )

    lost = "no rows attributed to this run in the research record (audit DATA-01)"
    snapshot = TelemetrySnapshot(
        captured_at=collected,
        collected_window=TimeWindow(start=start - timedelta(minutes=15), end=start),
        environment="opentelemetry-demo (research capture)",
        synthetic=False,
        description=f"held-out case {case['case_id']} converted from the research record",
        metrics=metrics,
        logs=logs,
        spans=spans,
        collection=[
            outcome(Modality.METRICS, len(metrics), "prometheus", lost),
            outcome(Modality.LOGS, len(logs), "opensearch", lost),
            outcome(Modality.TRACES, len(spans), "jaeger", lost),
            CollectionOutcome(
                modality=Modality.CHANGES,
                provider="none",
                status=CollectionStatus.NOT_CONFIGURED,
                detail="the research engine collected no change events",
            ),
        ],
    )
    request = AnalysisRequest(
        incident=TimeWindow(start=start - timedelta(minutes=15), end=start + timedelta(minutes=1)),
        baselines=[
            TimeWindow(start=start - timedelta(minutes=75), end=start - timedelta(minutes=15))
        ],
        focus_services=[case["reported_service"]] if case.get("reported_service") else [],
    )
    return snapshot, request


def run(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "eval/heldout/manifest.json").read_text(encoding="utf-8"))
    expected_doc = json.loads((root / "eval/heldout/expected.json").read_text(encoding="utf-8"))
    expected = {c["case_id"]: c["expected"] for c in expected_doc["cases"]}
    archive = json.load(
        gzip.open(root / "eval/archive/aegis_demo_db_export.json.gz", "rt", encoding="utf-8")
    )
    evidence_by_incident: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in archive["tables"]["evidence"]:
        evidence_by_incident[row["incident_id"]].append(row)

    results: list[dict[str, Any]] = []
    scores: list[Score] = []
    for case in manifest["cases"]:
        rows = sorted(evidence_by_incident[case["source_incident_id"]], key=lambda r: r["id"])
        fields = [
            "id",
            "type",
            "source_provider",
            "service_resource",
            "summary",
            "data",
            "observed_at",
            "collected_at",
        ]
        digest = hashlib.sha256(
            json.dumps([{k: r[k] for k in fields} for r in rows], sort_keys=True).encode()
        ).hexdigest()
        if digest != case["evidence_sha256"]:
            raise RuntimeError(f"{case['case_id']}: evidence does not match the frozen manifest")
        snapshot, request = build_case(case, rows)
        analysis: Analysis = analyze(snapshot, request)
        exp = expected[case["case_id"]]
        expectation = Expectation(
            classifications=exp["acceptable_classifications"],
            root_services=exp["acceptable_root_services_if_named"],
            must_mention=exp["must_mention_in_candidates_or_next_checks"],
            no_anomaly_allowed="NO_ANOMALY" in exp["acceptable_classifications"],
        )
        s = score(case["case_id"], analysis, expectation)
        if any(m.startswith("change_induced") for m in s.mechanisms):
            s.confident_wrong = True
            s.notes.append("change-induced mechanism claimed without any change evidence")
        scores.append(s)
        results.append(
            {
                **s.as_dict(),
                "scenario_id": case["scenario_id"],
                "display_label": analysis.verdict.display_label,
                "next_checks": [nc.action for nc in analysis.verdict.next_checks],
                "coverage_gaps": analysis.verdict.coverage_gaps,
                "candidates": [c.service for c in analysis.candidates][:6],
            }
        )
    return {
        "summary": {
            "cases": len(scores),
            "confident_wrong": sum(1 for s in scores if s.confident_wrong),
            "outcomes": {
                o: sum(1 for s in scores if s.outcome.value == o)
                for o in sorted({s.outcome.value for s in scores})
            },
            # LF-normalised, i.e. the hash of the committed blob (Windows checkouts use CRLF)
            "expected_sha256": hashlib.sha256(
                (root / "eval/heldout/expected.json").read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest(),
            "note": (
                "Real captures with no span status, no parent links, no change feed and no "
                "baseline window; see eval/heldout/manifest.json known_limitations."
            ),
        },
        "cases": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate on the frozen held-out set")
    parser.add_argument("--root", type=Path, default=Path(".."))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = run(args.root)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for c in report["cases"]:
        sys.stdout.write(
            f"{c['case_id']:<32} {c['outcome']:<24} {c['classification']:<12} roots={c['named_roots']}\n"
            f"{'':32}   label: {c['display_label'][:120]}\n"
        )
        for n in c["notes"]:
            sys.stdout.write(f"{'':32}   - {n}\n")
    sys.stdout.write(json.dumps(report["summary"], indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
