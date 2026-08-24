from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.domain import (
    Evidence,
    LogsProvider,
    MetricsProvider,
    ToolCall,
    ToolDefinition,
    ToolResult,
    TopologyProvider,
    TracesProvider,
)


class GetServiceHealthArgs(BaseModel):
    service_name: str = Field(..., max_length=100)


class QueryMetricsArgs(BaseModel):
    query: str = Field(..., max_length=200)


class SearchLogsArgs(BaseModel):
    query: str = Field(..., max_length=200)


class GetServiceDependenciesArgs(BaseModel):
    service_name: str = Field(..., max_length=100)


class GetTraceArgs(BaseModel):
    trace_id: str = Field(..., max_length=100)

class SearchTracesArgs(BaseModel):
    service_name: str = Field(..., max_length=100)


# Phase 3 error-focused tool argument models
class QueryErrorMetricsArgs(BaseModel):
    service_name: str = Field(..., max_length=100)


class SearchErrorLogsArgs(BaseModel):
    service_name: str = Field(..., max_length=100)


class SearchErrorTracesArgs(BaseModel):
    service_name: str = Field(..., max_length=100)


class _GenericCustomArgs(BaseModel):
    model_config = {"extra": "allow"}


class ToolDispatcher:
    def __init__(
        self,
        metrics_provider: MetricsProvider,
        logs_provider: LogsProvider,
        topology_provider: TopologyProvider,
        incident_start_time: datetime,
        traces_provider: TracesProvider = None, # type: ignore
    ):
        self.metrics_provider = metrics_provider
        self.logs_provider = logs_provider
        self.topology_provider = topology_provider
        self.traces_provider = traces_provider
        self.incident_start_time = incident_start_time

        from collections.abc import Callable
        self.registry: dict[str, tuple[Callable[..., Any], type[BaseModel]]] = {
            "get_service_health": (self._get_service_health, GetServiceHealthArgs),
            "query_metrics": (self._query_metrics, QueryMetricsArgs),
            "search_logs": (self._search_logs, SearchLogsArgs),
            "get_service_dependencies": (self._get_service_dependencies, GetServiceDependenciesArgs),
            # Phase 3 error-focused tools
            "query_error_metrics": (self._query_error_metrics, QueryErrorMetricsArgs),
            "search_error_logs": (self._search_error_logs, SearchErrorLogsArgs),
        }
        if self.traces_provider:
            self.registry["search_traces"] = (self._search_traces, SearchTracesArgs)
            self.registry["get_trace"] = (self._get_trace, GetTraceArgs)
            self.registry["search_error_traces"] = (self._search_error_traces, SearchErrorTracesArgs)

        self.custom_tool_definitions: list[ToolDefinition] = []

    def register_custom_tool(
        self,
        definition: ToolDefinition,
        handler: Any,
        args_model: type[BaseModel] | None = None,
    ) -> None:
        """Register an external or custom tool (e.g. from an MCP client adapter)."""
        if args_model is None:
            args_model = _GenericCustomArgs
        self.registry[definition.name] = (handler, args_model)
        self.custom_tool_definitions = [
            d for d in self.custom_tool_definitions if d.name != definition.name
        ]
        self.custom_tool_definitions.append(definition)

    def get_tool_definitions(self) -> list[ToolDefinition]:
        defs = [
            ToolDefinition(
                name="get_service_health",
                description="Get the high-level health status of a service (HEALTHY, DEGRADED, UNAVAILABLE).",
                parameters={
                    "service_name": {
                        "type": "string",
                        "description": "The name of the service to check",
                    }
                },
                required=["service_name"],
            ),
            ToolDefinition(
                name="query_metrics",
                description="Query metrics for a service or resource to find latency, errors, or throughput anomalies.",
                parameters={
                    "query": {
                        "type": "string",
                        "description": "A generic keyword or service name to query for metrics (e.g. 'frontend' or 'payment-api')",
                    }
                },
                required=["query"],
            ),
            ToolDefinition(
                name="search_logs",
                description="Search logs for errors or warnings.",
                parameters={
                    "query": {
                        "type": "string",
                        "description": "A descriptive query (e.g. 'payment-api errors')",
                    }
                },
                required=["query"],
            ),
            ToolDefinition(
                name="get_service_dependencies",
                description="Find the upstream and downstream dependencies of a service.",
                parameters={
                    "service_name": {"type": "string", "description": "The name of the service"}
                },
                required=["service_name"],
            ),
            # Phase 3 error-focused tools
            ToolDefinition(
                name="query_error_metrics",
                description="Query error-specific metrics for a service: HTTP 5xx error rates, error counts, and failed request ratios.",
                parameters={
                    "service_name": {"type": "string", "description": "The name of the service to check for errors"}
                },
                required=["service_name"],
            ),
            ToolDefinition(
                name="search_error_logs",
                description="Search specifically for ERROR and WARNING severity logs from a service, including exceptions and failure messages.",
                parameters={
                    "service_name": {"type": "string", "description": "The name of the service to search error logs for"}
                },
                required=["service_name"],
            ),
        ]
        
        if self.traces_provider:
            defs.extend([
                ToolDefinition(
                    name="search_traces",
                    description="Search for traces related to a specific service.",
                    parameters={
                        "service_name": {"type": "string", "description": "The name of the service (e.g. frontend)"}
                    },
                    required=["service_name"]
                ),
                ToolDefinition(
                    name="get_trace",
                    description="Get detailed information about a specific trace.",
                    parameters={
                        "trace_id": {"type": "string", "description": "The ID of the trace"}
                    },
                    required=["trace_id"]
                ),
                ToolDefinition(
                    name="search_error_traces",
                    description="Search for traces with error status codes or failure tags from a specific service.",
                    parameters={
                        "service_name": {"type": "string", "description": "The name of the service to search for error traces"}
                    },
                    required=["service_name"],
                ),
            ])

        defs.extend(self.custom_tool_definitions)
        return defs

    def dispatch(self, tool_call: ToolCall) -> ToolResult | str:
        """Executes a tool call and returns the result."""
        entry = self.registry.get(tool_call.name)
        if not entry:
            return f"Error: Tool '{tool_call.name}' not found."

        handler, model_cls = entry

        try:
            # Pydantic validation
            validated_args = model_cls(**tool_call.arguments)
        except ValidationError as e:
            return f"Error executing tool '{tool_call.name}': Invalid arguments. Details:\n{e}"

        try:
            return handler(**validated_args.model_dump()) # type: ignore
        except Exception as e:
            return f"Error executing tool '{tool_call.name}': {e!s}"

    def _get_service_health(self, service_name: str) -> ToolResult:
        health = self.metrics_provider.get_service_health(service_name)
        return ToolResult(summary={"service": service_name, "status": health.status.value})

    def _query_metrics(self, query: str) -> ToolResult:
        # Use a 15-minute lookback window
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=15)
        evidence_list = self.metrics_provider.query_metrics(query, start_time, end_time)
        return ToolResult(evidence=evidence_list, summary=self._summarize_evidence(evidence_list))

    def _search_logs(self, query: str) -> ToolResult:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=15)
        evidence_list = self.logs_provider.search_logs(query, start_time, end_time)
        return ToolResult(evidence=evidence_list, summary=self._summarize_evidence(evidence_list))

    def _get_service_dependencies(self, service_name: str) -> ToolResult:
        deps = self.topology_provider.get_service_dependencies(service_name)
        return ToolResult(summary={"service": service_name, "dependencies": deps.get(service_name, [])})

    def _search_traces(self, service_name: str) -> ToolResult:
        if not self.traces_provider:
            return ToolResult(summary={"error": "TracesProvider not configured"})
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=15)
        evidence_list = self.traces_provider.get_related_traces(service_name, start_time, end_time)
        return ToolResult(evidence=evidence_list, summary=self._summarize_evidence(evidence_list))
        
    def _get_trace(self, trace_id: str) -> ToolResult:
        if not self.traces_provider:
            return ToolResult(summary={"error": "TracesProvider not configured"})
        ev = self.traces_provider.get_trace(trace_id)
        if not ev:
            return ToolResult(summary={"error": f"Trace {trace_id} not found"})
        return ToolResult(evidence=[ev], summary=self._summarize_evidence([ev]))

    # ---- Phase 3 error-focused tool implementations ----

    def _query_error_metrics(self, service_name: str) -> ToolResult:
        """Query specifically for error-rate metrics for a service.
        
        Constructs PromQL that targets HTTP 5xx responses rather than
        generic request counts. Falls back to the generic query if the
        provider doesn't support error-specific queries.
        """
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=15)
        # Construct error-specific PromQL
        error_query = (
            f'http_server_request_duration_seconds_count'
            f'{{service_name=~".*{service_name}.*",http_response_status_code=~"5.."}}'
        )
        evidence_list = self.metrics_provider.query_metrics(error_query, start_time, end_time)
        
        # Also query total for context (so the LLM can compute error rate)
        total_query = (
            f'http_server_request_duration_seconds_count'
            f'{{service_name=~".*{service_name}.*"}}'
        )
        total_evidence = self.metrics_provider.query_metrics(total_query, start_time, end_time)
        
        # Combine: error metrics first, then total for context
        all_evidence = evidence_list + total_evidence
        
        # Build a diagnostic summary
        summary = {
            "tool": "query_error_metrics",
            "service": service_name,
            "error_series_count": len(evidence_list),
            "total_series_count": len(total_evidence),
            "error_details": self._summarize_evidence(evidence_list),
            "total_details": self._summarize_evidence(total_evidence[:3]),  # Bounded
        }
        return ToolResult(evidence=all_evidence, summary=summary)

    def _search_error_logs(self, service_name: str) -> ToolResult:
        """Search specifically for ERROR/WARNING severity logs from a service.
        
        Pre-filters the query for error-related terms and severity levels.
        """
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=15)
        error_query = f"({service_name}) AND (ERROR OR error OR Exception OR fail OR 500 OR timeout OR WARN)"
        evidence_list = self.logs_provider.search_logs(error_query, start_time, end_time)
        
        # Build diagnostic summary with error-specific information
        error_count = len(evidence_list)
        severity_counts: dict[str, int] = {}
        sample_messages: list[str] = []
        
        for e in evidence_list[:20]:  # Inspect first 20
            data = e.data if isinstance(e.data, dict) else {}
            level = data.get("level", "UNKNOWN")
            severity_counts[level] = severity_counts.get(level, 0) + 1
            if len(sample_messages) < 3:
                msg = data.get("message", e.summary)
                if isinstance(msg, str) and msg:
                    sample_messages.append(msg[:120])
        
        summary = {
            "tool": "search_error_logs",
            "service": service_name,
            "total_error_logs": error_count,
            "severity_breakdown": severity_counts,
            "sample_error_messages": sample_messages,
            "evidence_details": self._summarize_evidence(evidence_list[:5]),  # Bounded
        }
        return ToolResult(evidence=evidence_list, summary=summary)

    def _search_error_traces(self, service_name: str) -> ToolResult:
        """Search for traces with error status from a specific service.
        
        Fetches all recent traces and filters for those containing error tags.
        """
        if not self.traces_provider:
            return ToolResult(summary={"error": "TracesProvider not configured"})
        
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=15)
        all_traces = self.traces_provider.get_related_traces(service_name, start_time, end_time)
        
        # Filter for error traces by checking evidence data and summary
        error_traces = []
        healthy_traces = []
        for ev in all_traces:
            summary_lower = (ev.summary or "").lower()
            data = ev.data if isinstance(ev.data, dict) else {}
            spans = data.get("spans", [])
            
            has_error = (
                "error" in summary_lower or
                "fail" in summary_lower or
                "500" in summary_lower or
                any(
                    span.get("operation", "").lower() in ("error", "fail") or
                    "error" in str(span.get("service", "")).lower()
                    for span in spans
                )
            )
            if has_error:
                error_traces.append(ev)
            else:
                healthy_traces.append(ev)
        
        summary = {
            "tool": "search_error_traces",
            "service": service_name,
            "total_traces": len(all_traces),
            "error_traces": len(error_traces),
            "healthy_traces": len(healthy_traces),
            "error_trace_details": self._summarize_evidence(error_traces[:5]),  # Bounded
        }
        # Return all traces but surface error traces prominently in summary
        return ToolResult(evidence=all_traces, summary=summary)

    def _summarize_evidence(self, evidence_list: list[Evidence]) -> list[dict[str, Any]]:
        """Compact representation of evidence for the LLM context window."""
        summary = []
        for e in evidence_list:
            summary.append(
                {
                    "id": e.id,
                    "type": e.type.value,
                    "service": e.service_resource,
                    "summary": e.summary,
                }
            )
        return summary
