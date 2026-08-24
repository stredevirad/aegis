"""Tests for the Phase 3 evidence summarizer."""
from datetime import datetime, timezone

from src.domain import Evidence, EvidenceType
from src.investigation.evidence_summarizer import (
    format_evidence_summary_for_llm,
    summarize_evidence,
)


def _make_evidence(
    eid: str, etype: EvidenceType, summary: str,
    service: str = "test-svc", data: dict | None = None,
) -> Evidence:
    now = datetime.now(timezone.utc)
    return Evidence(
        id=eid, type=etype, source_provider="test",
        service_resource=service, summary=summary,
        data=data or {}, observed_at=now, collected_at=now,
    )


class TestSummarizeEvidence:
    def test_empty_evidence(self):
        result = summarize_evidence([])
        assert result["evidence_inventory"]["total"] == 0
        assert result["error_signals"]["has_errors"] is False

    def test_counts_by_type(self):
        evidence = [
            _make_evidence("m1", EvidenceType.METRIC, "metric 1"),
            _make_evidence("m2", EvidenceType.METRIC, "metric 2"),
            _make_evidence("l1", EvidenceType.LOG, "log 1"),
            _make_evidence("t1", EvidenceType.TRACE, "trace 1"),
        ]
        result = summarize_evidence(evidence)
        inv = result["evidence_inventory"]
        assert inv["total"] == 4
        assert inv["metrics"] == 2
        assert inv["logs"] == 1
        assert inv["traces"] == 1

    def test_detects_error_logs(self):
        evidence = [
            _make_evidence("l1", EvidenceType.LOG, "[ERROR] connection refused",
                          service="frontend", data={"level": "ERROR", "message": "connection refused"}),
            _make_evidence("l2", EvidenceType.LOG, "[INFO] normal operation",
                          service="frontend", data={"level": "INFO", "message": "normal operation"}),
        ]
        result = summarize_evidence(evidence)
        assert result["error_signals"]["has_errors"] is True
        assert result["error_signals"]["error_log_count"] == 1
        assert "frontend" in result["error_signals"]["error_services"]

    def test_detects_error_traces(self):
        evidence = [
            _make_evidence("t1", EvidenceType.TRACE, "Trace: error in product-catalog",
                          service="product-catalog"),
            _make_evidence("t2", EvidenceType.TRACE, "Trace: normal GET /api/products",
                          service="frontend"),
        ]
        result = summarize_evidence(evidence)
        assert result["error_signals"]["error_trace_count"] == 1
        assert result["error_signals"]["has_errors"] is True

    def test_service_activity(self):
        evidence = [
            _make_evidence("m1", EvidenceType.METRIC, "m", service="frontend"),
            _make_evidence("m2", EvidenceType.METRIC, "m", service="frontend"),
            _make_evidence("l1", EvidenceType.LOG, "l", service="backend"),
        ]
        result = summarize_evidence(evidence)
        activity = result["service_activity"]
        assert activity["frontend"] == 2
        assert activity["backend"] == 1

    def test_log_severity_breakdown(self):
        evidence = [
            _make_evidence("l1", EvidenceType.LOG, "e", data={"level": "ERROR", "message": "err"}),
            _make_evidence("l2", EvidenceType.LOG, "w", data={"level": "WARN", "message": "warn"}),
            _make_evidence("l3", EvidenceType.LOG, "i", data={"level": "INFO", "message": "info"}),
        ]
        result = summarize_evidence(evidence)
        breakdown = result["logs_summary"]["severity_breakdown"]
        assert breakdown["ERROR"] == 1
        assert breakdown["WARN"] == 1
        assert breakdown["INFO"] == 1

    def test_trace_latency_stats(self):
        evidence = [
            _make_evidence("t1", EvidenceType.TRACE, "Trace: op | Root: svc | Duration: 100.00ms\nSpans: 3"),
            _make_evidence("t2", EvidenceType.TRACE, "Trace: op | Root: svc | Duration: 200.00ms\nSpans: 5"),
            _make_evidence("t3", EvidenceType.TRACE, "Trace: op | Root: svc | Duration: 500.00ms\nSpans: 2"),
        ]
        result = summarize_evidence(evidence)
        stats = result["latency_signals"]["trace_latency_stats"]
        assert stats is not None
        assert stats["count"] == 3
        assert stats["max_ms"] == 500.0

    def test_no_errors_when_healthy(self):
        evidence = [
            _make_evidence("m1", EvidenceType.METRIC, "normal metric"),
            _make_evidence("l1", EvidenceType.LOG, "info log", data={"level": "INFO", "message": "ok"}),
            _make_evidence("t1", EvidenceType.TRACE, "Trace: normal | Duration: 50.00ms"),
        ]
        result = summarize_evidence(evidence)
        assert result["error_signals"]["has_errors"] is False
        assert result["error_signals"]["error_log_count"] == 0


class TestFormatForLLM:
    def test_produces_string(self):
        evidence = [
            _make_evidence("m1", EvidenceType.METRIC, "metric"),
            _make_evidence("l1", EvidenceType.LOG, "[ERROR] err", data={"level": "ERROR", "message": "fail"}),
        ]
        summary = summarize_evidence(evidence)
        text = format_evidence_summary_for_llm(summary)
        assert isinstance(text, str)
        assert "EVIDENCE INVENTORY" in text
        assert "ERROR SIGNALS" in text

    def test_no_errors_format(self):
        evidence = [
            _make_evidence("m1", EvidenceType.METRIC, "normal"),
        ]
        summary = summarize_evidence(evidence)
        text = format_evidence_summary_for_llm(summary)
        assert "No error signals detected" in text

    def test_error_format_includes_messages(self):
        evidence = [
            _make_evidence("l1", EvidenceType.LOG, "[ERROR] connection timeout",
                          service="product-catalog",
                          data={"level": "ERROR", "message": "connection timeout to upstream"}),
        ]
        summary = summarize_evidence(evidence)
        text = format_evidence_summary_for_llm(summary)
        assert "product-catalog" in text
        assert "connection timeout" in text
