"""Evidence Summarizer — structured pre-LLM evidence summary.

Phase 3.3: Produces a structured summary of collected evidence that highlights
diagnostic signals rather than dumping raw data. This gives the LLM a more
actionable view of the telemetry state.

The summarizer processes raw Evidence objects and produces a compact diagnostic
report with sections for:
- Error Signals (error rates, error logs, error traces)
- Latency Anomalies (high-latency metrics/traces)
- Service Activity (which services appear in the evidence)
- Evidence Inventory (counts by type)
"""
from typing import Any

from src.domain import Evidence, EvidenceType


def summarize_evidence(evidence_list: list[Evidence]) -> dict[str, Any]:
    """Produce a structured diagnostic summary from a list of Evidence objects.
    
    Returns a dict suitable for JSON serialization and inclusion in the LLM prompt.
    """
    # Partition by type
    metrics: list[Evidence] = []
    logs: list[Evidence] = []
    traces: list[Evidence] = []
    
    for e in evidence_list:
        if e.type == EvidenceType.METRIC:
            metrics.append(e)
        elif e.type == EvidenceType.LOG:
            logs.append(e)
        elif e.type == EvidenceType.TRACE:
            traces.append(e)
    
    return {
        "evidence_inventory": {
            "total": len(evidence_list),
            "metrics": len(metrics),
            "logs": len(logs),
            "traces": len(traces),
        },
        "error_signals": _extract_error_signals(metrics, logs, traces),
        "latency_signals": _extract_latency_signals(metrics, traces),
        "service_activity": _extract_service_activity(evidence_list),
        "metrics_summary": _summarize_metrics(metrics),
        "logs_summary": _summarize_logs(logs),
        "traces_summary": _summarize_traces(traces),
    }


def _extract_error_signals(
    metrics: list[Evidence],
    logs: list[Evidence],
    traces: list[Evidence],
) -> dict[str, Any]:
    """Extract error-specific signals across all modalities."""
    error_signals: dict[str, Any] = {
        "has_errors": False,
        "error_log_count": 0,
        "error_services": [],
        "sample_error_messages": [],
        "error_trace_count": 0,
        "error_metric_series": 0,
    }
    
    # Error logs
    error_services: set[str] = set()
    error_messages: list[str] = []
    for log in logs:
        data = log.data if isinstance(log.data, dict) else {}
        level = data.get("level", "").upper()
        summary_lower = (log.summary or "").lower()
        
        if level in ("ERROR", "CRITICAL", "FATAL") or "error" in summary_lower or "500" in summary_lower:
            error_signals["error_log_count"] += 1
            error_signals["has_errors"] = True
            if log.service_resource:
                error_services.add(log.service_resource)
            msg = data.get("message", log.summary)
            if isinstance(msg, str) and msg and len(error_messages) < 5:
                error_messages.append(msg[:150])
    
    # Error traces
    for trace in traces:
        summary_lower = (trace.summary or "").lower()
        if "error" in summary_lower or "fail" in summary_lower:
            error_signals["error_trace_count"] += 1
            error_signals["has_errors"] = True
            if trace.service_resource:
                error_services.add(trace.service_resource)
    
    # Error metrics (look for 5xx status codes in metric labels)
    for metric in metrics:
        data = metric.data if isinstance(metric.data, dict) else {}
        metric_labels = data.get("metric", {})
        status_code = metric_labels.get("http_response_status_code", "")
        if str(status_code).startswith("5"):
            error_signals["error_metric_series"] += 1
            error_signals["has_errors"] = True
            if metric.service_resource:
                error_services.add(metric.service_resource)
    
    error_signals["error_services"] = sorted(error_services)
    error_signals["sample_error_messages"] = error_messages
    
    return error_signals


def _extract_latency_signals(
    metrics: list[Evidence],
    traces: list[Evidence],
) -> dict[str, Any]:
    """Extract latency-related signals."""
    latency_signals: dict[str, Any] = {
        "high_latency_traces": 0,
        "slowest_trace": None,
        "trace_latency_stats": None,
    }
    
    # Analyze trace durations
    if traces:
        durations: list[float] = []
        slowest: dict[str, Any] | None = None
        max_duration = -1.0
        
        for trace in traces:
            summary = trace.summary or ""
            # Parse duration from summary format: "Duration: X.XXms"
            import re
            dur_match = re.search(r"Duration:\s*([\d.]+)ms", summary)
            if dur_match:
                dur = float(dur_match.group(1))
                durations.append(dur)
                if dur > max_duration:
                    max_duration = dur
                    slowest = {
                        "id": trace.id,
                        "service": trace.service_resource,
                        "duration_ms": dur,
                        "summary": summary[:100],
                    }
        
        if durations:
            durations.sort()
            n = len(durations)
            p50 = durations[n // 2] if n > 0 else 0
            p95 = durations[int(n * 0.95)] if n > 1 else durations[-1]
            p99 = durations[int(n * 0.99)] if n > 1 else durations[-1]
            mean = sum(durations) / n
            
            # Count high-latency traces (> 2x median)
            threshold = p50 * 2 if p50 > 0 else 1000
            latency_signals["high_latency_traces"] = sum(1 for d in durations if d > threshold)
            latency_signals["slowest_trace"] = slowest
            latency_signals["trace_latency_stats"] = {
                "count": n,
                "mean_ms": round(mean, 2),
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "p99_ms": round(p99, 2),
                "max_ms": round(max_duration, 2),
            }
    
    return latency_signals


def _extract_service_activity(evidence_list: list[Evidence]) -> dict[str, int]:
    """Count evidence items per service."""
    service_counts: dict[str, int] = {}
    for e in evidence_list:
        svc = e.service_resource or "unknown"
        service_counts[svc] = service_counts.get(svc, 0) + 1
    # Sort by count descending
    return dict(sorted(service_counts.items(), key=lambda x: x[1], reverse=True))


def _summarize_metrics(metrics: list[Evidence]) -> dict[str, Any]:
    """Summarize metric evidence."""
    if not metrics:
        return {"count": 0, "note": "No metrics collected"}
    
    services: set[str] = set()
    metric_names: set[str] = set()
    
    for m in metrics:
        if m.service_resource:
            services.add(m.service_resource)
        data = m.data if isinstance(m.data, dict) else {}
        metric_labels = data.get("metric", {})
        name = metric_labels.get("__name__", "")
        if name:
            metric_names.add(name)
    
    # Compute value trends from the first few metrics
    value_samples: list[dict[str, Any]] = []
    for m in metrics[:5]:
        data = m.data if isinstance(m.data, dict) else {}
        values = data.get("values", [])
        if values and len(values) >= 2:
            first_val = float(values[0][1]) if len(values[0]) > 1 else 0
            last_val = float(values[-1][1]) if len(values[-1]) > 1 else 0
            trend = "increasing" if last_val > first_val * 1.1 else (
                "decreasing" if last_val < first_val * 0.9 else "stable"
            )
            value_samples.append({
                "service": m.service_resource,
                "metric": data.get("metric", {}).get("__name__", "unknown"),
                "first": first_val,
                "last": last_val,
                "trend": trend,
                "data_points": len(values),
            })
    
    return {
        "count": len(metrics),
        "services": sorted(services),
        "metric_names": sorted(metric_names),
        "value_trends": value_samples,
    }


def _summarize_logs(logs: list[Evidence]) -> dict[str, Any]:
    """Summarize log evidence with severity breakdown and deduplication."""
    if not logs:
        return {"count": 0, "note": "No logs collected"}
    
    severity_counts: dict[str, int] = {}
    message_counts: dict[str, int] = {}
    services: set[str] = set()
    
    for log in logs:
        if log.service_resource:
            services.add(log.service_resource)
        data = log.data if isinstance(log.data, dict) else {}
        level = data.get("level", "UNKNOWN")
        severity_counts[level] = severity_counts.get(level, 0) + 1
        
        # Deduplicate messages (truncate for grouping)
        msg = data.get("message", log.summary)
        if isinstance(msg, str):
            msg_key = msg[:80]
            message_counts[msg_key] = message_counts.get(msg_key, 0) + 1
    
    # Top-5 most frequent messages
    top_messages = sorted(message_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    
    return {
        "count": len(logs),
        "services": sorted(services),
        "severity_breakdown": severity_counts,
        "top_messages": [{"message": m, "count": c} for m, c in top_messages],
        "unique_messages": len(message_counts),
    }


def _summarize_traces(traces: list[Evidence]) -> dict[str, Any]:
    """Summarize trace evidence."""
    if not traces:
        return {"count": 0, "note": "No traces collected"}
    
    services: set[str] = set()
    total_spans = 0
    
    for trace in traces:
        if trace.service_resource:
            services.add(trace.service_resource)
        data = trace.data if isinstance(trace.data, dict) else {}
        total_spans += data.get("total_spans", len(data.get("spans", [])))
    
    return {
        "count": len(traces),
        "services": sorted(services),
        "total_spans": total_spans,
        "avg_spans_per_trace": round(total_spans / len(traces), 1) if traces else 0,
    }


def format_evidence_summary_for_llm(summary: dict[str, Any]) -> str:
    """Format the structured summary into a compact text block for the LLM prompt."""
    
    lines: list[str] = []
    
    inv = summary["evidence_inventory"]
    lines.append("=== EVIDENCE INVENTORY ===")
    lines.append(f"Total: {inv['total']} (Metrics: {inv['metrics']}, Logs: {inv['logs']}, Traces: {inv['traces']})")
    
    errors = summary["error_signals"]
    if errors["has_errors"]:
        lines.append("\n=== ERROR SIGNALS (DIAGNOSTIC PRIORITY) ===")
        lines.append(f"Error logs: {errors['error_log_count']}")
        lines.append(f"Error traces: {errors['error_trace_count']}")
        lines.append(f"Error metric series: {errors['error_metric_series']}")
        if errors["error_services"]:
            lines.append(f"Affected services: {', '.join(errors['error_services'])}")
        if errors["sample_error_messages"]:
            lines.append("Sample error messages:")
            for msg in errors["sample_error_messages"][:3]:
                lines.append(f"  - {msg}")
    else:
        lines.append("\n=== ERROR SIGNALS ===")
        lines.append("No error signals detected across any modality.")
    
    latency = summary["latency_signals"]
    if latency.get("trace_latency_stats"):
        stats = latency["trace_latency_stats"]
        lines.append("\n=== LATENCY SIGNALS ===")
        lines.append(f"Trace count: {stats['count']}")
        lines.append(f"Latency: p50={stats['p50_ms']}ms, p95={stats['p95_ms']}ms, max={stats['max_ms']}ms")
        if latency["high_latency_traces"] > 0:
            lines.append(f"High-latency traces (>2x p50): {latency['high_latency_traces']}")
    
    svc = summary["service_activity"]
    if svc:
        lines.append("\n=== SERVICE ACTIVITY ===")
        for service, count in list(svc.items())[:8]:
            lines.append(f"  {service}: {count} evidence items")
    
    logs_sum = summary["logs_summary"]
    if logs_sum.get("severity_breakdown"):
        lines.append("\n=== LOG SEVERITY ===")
        for sev, count in sorted(logs_sum["severity_breakdown"].items()):
            lines.append(f"  {sev}: {count}")
    
    return "\n".join(lines)
