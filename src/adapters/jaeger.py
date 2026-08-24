from datetime import datetime, timezone
from typing import Any

import httpx

from src.domain import Evidence, EvidenceType, ProviderStatus, TracesProvider
from src.domain.models import HealthStatus


class JaegerProvider(TracesProvider):
    def __init__(self, endpoint: str):
        self.endpoint = endpoint.rstrip("/")
        # The API endpoints are under /jaeger/ui/api based on OTel demo ingress
        self.api_base = f"{self.endpoint}/jaeger/ui/api"

    def get_status(self) -> ProviderStatus:
        try:
            # A simple GET to /services acts as a health check
            response = httpx.get(f"{self.api_base}/services", timeout=5.0)
            if response.status_code == 200:
                return ProviderStatus(
                    provider="jaeger",
                    health=HealthStatus.HEALTHY,
                    capabilities=["search_traces", "get_trace"]
                )
        except httpx.RequestError:
            pass
            
        return ProviderStatus(
            provider="jaeger",
            health=HealthStatus.UNAVAILABLE,
            capabilities=[]
        )

    def get_trace(self, trace_id: str) -> Evidence | None:
        try:
            response = httpx.get(f"{self.api_base}/traces/{trace_id}", timeout=10.0)
            if response.status_code != 200:
                return None
            
            data = response.json()
            traces = data.get("data", [])
            if not traces:
                return None
                
            return self._normalize_trace(traces[0])
        except (httpx.RequestError, ValueError) as e:
            raise RuntimeError(f"Failed to fetch trace {trace_id}: {e!s}")

    def get_related_traces(
        self, service_name: str, start_time: datetime, end_time: datetime
    ) -> list[Evidence]:
        if start_time > end_time:
            raise ValueError("start_time cannot be after end_time")
            
        window = end_time - start_time
        if window.total_seconds() > 12 * 3600:
            raise ValueError(f"Trace query window {window} exceeds the 12-hour limit.")

        start_microseconds = int(start_time.timestamp() * 1_000_000)
        end_microseconds = int(end_time.timestamp() * 1_000_000)

        params: dict[str, str | int] = {
            "service": service_name,
            "start": start_microseconds,
            "end": end_microseconds,
            "limit": 50, # Conservative bound
        }
        
        try:
            response = httpx.get(f"{self.api_base}/traces", params=params, timeout=10.0)
            if response.status_code != 200:
                raise RuntimeError(f"Jaeger returned status {response.status_code}")
                
            data = response.json()
            traces = data.get("data", [])
            
            evidence_list = []
            for trace in traces:
                normalized = self._normalize_trace(trace)
                if normalized:
                    evidence_list.append(normalized)
                    
            return evidence_list
        except (httpx.RequestError, ValueError) as e:
            raise RuntimeError(f"Failed to fetch related traces: {e!s}")

    def _normalize_trace(self, trace: dict[str, Any]) -> Evidence | None:
        trace_id = trace.get("traceID")
        if not trace_id:
            return None
            
        spans = trace.get("spans", [])
        processes = trace.get("processes", {})
        
        if not spans:
            return None
            
        # Find root span (span without references or parent)
        # Note: In Jaeger, parent references are usually in `references`
        root_span = None
        for span in spans:
            refs = span.get("references", [])
            is_child = any(ref.get("refType") == "CHILD_OF" for ref in refs)
            if not is_child:
                root_span = span
                break
                
        if not root_span:
            root_span = spans[0] # Fallback to first
            
        # Extract service name
        process_id = root_span.get("processID")
        service_name = "unknown-service"
        if process_id and process_id in processes:
            service_name = processes[process_id].get("serviceName", "unknown-service")
            
        # Convert start time (microseconds) to datetime
        start_time_us = root_span.get("startTime", 0)
        observed_at = datetime.fromtimestamp(start_time_us / 1_000_000, tz=timezone.utc)
        
        # Structure the data to be compact for deep inspection if needed
        operation = root_span.get("operationName", "unknown-operation")
        total_duration_ms = root_span.get("duration", 0) / 1000.0
        span_count = len(spans)
        
        structured_spans = []
        services_seen = set()
        slowest_span = None
        max_duration = -1
        
        for span in spans[:50]: # Cap spans for deep inspection
            span_process = span.get("processID")
            span_service = processes.get(span_process, {}).get("serviceName", "unknown") if span_process else "unknown"
            services_seen.add(span_service)
            
            dur = span.get("duration", 0) / 1000.0
            structured_spans.append({
                "span_id": span.get("spanID"),
                "operation": span.get("operationName"),
                "service": span_service,
                "duration_ms": dur,
            })
            if dur > max_duration:
                max_duration = dur
                slowest_span = structured_spans[-1]

        # Detailed but compact summary for the LLM
        slowest_info = f"{slowest_span['service']}::{slowest_span['operation']} ({slowest_span['duration_ms']:.2f}ms)" if slowest_span else "none"
        summary = (
            f"Trace: {operation} | Root: {service_name} | Duration: {total_duration_ms:.2f}ms\n"
            f"Spans: {span_count} | Services: {', '.join(list(services_seen)[:5])}\n"
            f"Slowest: {slowest_info}"
        )

        evidence_id = f"ev_trace_{trace_id}"
        return Evidence(
            id=evidence_id,
            type=EvidenceType.TRACE,
            source_provider="jaeger",
            service_resource=service_name,
            summary=summary,
            observed_at=observed_at,
            collected_at=datetime.now(timezone.utc),
            data={
                "trace_id": trace_id,
                "spans": structured_spans,
                "total_spans": span_count,
            }
        )
