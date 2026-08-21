from datetime import datetime, timezone
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
)


class GetServiceHealthArgs(BaseModel):
    service_name: str = Field(..., max_length=100)


class QueryMetricsArgs(BaseModel):
    query: str = Field(..., max_length=200)


class SearchLogsArgs(BaseModel):
    query: str = Field(..., max_length=200)


class GetServiceDependenciesArgs(BaseModel):
    service_name: str = Field(..., max_length=100)


class ToolDispatcher:
    def __init__(
        self,
        metrics_provider: MetricsProvider,
        logs_provider: LogsProvider,
        topology_provider: TopologyProvider,
        incident_start_time: datetime,
    ):
        self.metrics_provider = metrics_provider
        self.logs_provider = logs_provider
        self.topology_provider = topology_provider
        self.incident_start_time = incident_start_time

        from collections.abc import Callable
        self.registry: dict[str, tuple[Callable[..., Any], type[BaseModel]]] = {
            "get_service_health": (self._get_service_health, GetServiceHealthArgs),
            "query_metrics": (self._query_metrics, QueryMetricsArgs),
            "search_logs": (self._search_logs, SearchLogsArgs),
            "get_service_dependencies": (self._get_service_dependencies, GetServiceDependenciesArgs),
        }

    def get_tool_definitions(self) -> list[ToolDefinition]:
        return [
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
                        "description": "A descriptive query specifying what to look for (e.g. 'payment-api latency')",
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
        ]

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
        # Use incident start time to current time as the bounded window
        end_time = datetime.now(timezone.utc)
        start_time = self.incident_start_time
        evidence_list = self.metrics_provider.query_metrics(query, start_time, end_time)
        return ToolResult(evidence=evidence_list, summary=self._summarize_evidence(evidence_list))

    def _search_logs(self, query: str) -> ToolResult:
        end_time = datetime.now(timezone.utc)
        start_time = self.incident_start_time
        evidence_list = self.logs_provider.search_logs(query, start_time, end_time)
        return ToolResult(evidence=evidence_list, summary=self._summarize_evidence(evidence_list))

    def _get_service_dependencies(self, service_name: str) -> ToolResult:
        deps = self.topology_provider.get_service_dependencies(service_name)
        return ToolResult(summary={"service": service_name, "dependencies": deps.get(service_name, [])})

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
                    "data": e.data,
                }
            )
        return summary
