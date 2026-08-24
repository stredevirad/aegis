"""Model Context Protocol (MCP) Server Adapter for Aegis.

Phase 4A: Exposes read-only Aegis investigation and telemetry querying
capabilities through the Model Context Protocol (MCP) without replacing or
modifying the core provider or domain abstractions.
"""
from datetime import datetime, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from src.domain import (
    Evidence,
    LogsProvider,
    MetricsProvider,
    ToolCall,
    ToolResult,
    TopologyProvider,
    TracesProvider,
)
from src.investigation.tools import ToolDispatcher
from src.persistence.repository import SQLiteRepository


def serialize_evidence(e: Evidence) -> dict[str, Any]:
    """Serialize an Evidence domain object into a clean dictionary with provenance metadata."""
    return {
        "id": e.id,
        "type": e.type.value if hasattr(e.type, "value") else str(e.type),
        "source_provider": e.source_provider,
        "service_resource": e.service_resource,
        "summary": e.summary,
        "observed_at": e.observed_at.isoformat() if e.observed_at else None,
        "collected_at": e.collected_at.isoformat() if e.collected_at else None,
        "data": e.data if isinstance(e.data, dict) else {"raw": str(e.data)},
    }


def serialize_tool_result(result: ToolResult | str) -> dict[str, Any]:
    """Serialize a ToolResult or error string into an MCP-compliant response payload."""
    if isinstance(result, str):
        is_error = result.startswith("Error")
        return {
            "status": "error" if is_error else "success",
            "message": result,
            "evidence_count": 0,
            "evidence": [],
            "summary": result,
        }

    return {
        "status": "success",
        "evidence_count": len(result.evidence),
        "evidence": [serialize_evidence(e) for e in result.evidence],
        "summary": result.summary,
    }


class AegisMCPServer:
    """MCP Server wrapping Aegis's ToolDispatcher and Persistence Repository.
    
    Provides bounded, read-only telemetry queries and Evidence inspection tools
    to external MCP-compatible agents (e.g., Claude Desktop, IDE sidecars) while
    preserving strict domain isolation and evidence provenance.
    """

    def __init__(
        self,
        tool_dispatcher: ToolDispatcher,
        repository: SQLiteRepository | None = None,
        name: str = "aegis-investigation-server",
        version: str = "1.1.0",
    ) -> None:
        self.tool_dispatcher = tool_dispatcher
        self.repository = repository
        self.mcp = MCPServer(name=name, version=version)
        self._register_tools()

    def _register_tools(self) -> None:
        """Register bounded read-only investigation tools on the MCP server."""

        @self.mcp.tool(
            name="query_metrics",
            description="Query metrics for a service or resource to find latency, error rate, or throughput anomalies within a bounded 15-minute window.",
        )
        def query_metrics(
            query: str = Field(
                ...,
                max_length=200,
                description="PromQL query or service name to query metrics for (e.g. 'frontend' or 'product-catalog')",
            ),
        ) -> dict[str, Any]:
            try:
                call = ToolCall(id=f"mcp_metric_{int(datetime.now(timezone.utc).timestamp()*1000)}", name="query_metrics", arguments={"query": query})
                result = self.tool_dispatcher.dispatch(call)
                return serialize_tool_result(result)
            except Exception as exc:
                return {"status": "error", "error": str(exc), "evidence_count": 0, "evidence": []}

        @self.mcp.tool(
            name="search_logs",
            description="Search logs for errors, warnings, or service events within a bounded 15-minute window.",
        )
        def search_logs(
            query: str = Field(
                ...,
                max_length=200,
                description="Search query string or service name (e.g. 'product-catalog' or 'payment-api errors')",
            ),
        ) -> dict[str, Any]:
            try:
                call = ToolCall(id=f"mcp_log_{int(datetime.now(timezone.utc).timestamp()*1000)}", name="search_logs", arguments={"query": query})
                result = self.tool_dispatcher.dispatch(call)
                return serialize_tool_result(result)
            except Exception as exc:
                return {"status": "error", "error": str(exc), "evidence_count": 0, "evidence": []}

        @self.mcp.tool(
            name="search_traces",
            description="Search distributed traces related to a specific service within a bounded 15-minute window.",
        )
        def search_traces(
            service_name: str = Field(
                ...,
                max_length=100,
                description="Name of the service to query traces for (e.g. 'frontend', 'product-catalog')",
            ),
        ) -> dict[str, Any]:
            try:
                call = ToolCall(id=f"mcp_trace_{int(datetime.now(timezone.utc).timestamp()*1000)}", name="search_traces", arguments={"service_name": service_name})
                result = self.tool_dispatcher.dispatch(call)
                return serialize_tool_result(result)
            except Exception as exc:
                return {"status": "error", "error": str(exc), "evidence_count": 0, "evidence": []}

        @self.mcp.tool(
            name="get_trace",
            description="Get detailed span breakdown, latency hierarchy, and error tags for a specific distributed trace ID.",
        )
        def get_trace(
            trace_id: str = Field(
                ...,
                max_length=100,
                description="The unique distributed trace ID to inspect",
            ),
        ) -> dict[str, Any]:
            try:
                call = ToolCall(id=f"mcp_gettrace_{int(datetime.now(timezone.utc).timestamp()*1000)}", name="get_trace", arguments={"trace_id": trace_id})
                result = self.tool_dispatcher.dispatch(call)
                return serialize_tool_result(result)
            except Exception as exc:
                return {"status": "error", "error": str(exc), "evidence_count": 0, "evidence": []}

        @self.mcp.tool(
            name="get_service_health",
            description="Get the high-level operational health status of a service (HEALTHY, DEGRADED, UNAVAILABLE, UNKNOWN).",
        )
        def get_service_health(
            service_name: str = Field(
                ...,
                max_length=100,
                description="The name of the service to check health for",
            ),
        ) -> dict[str, Any]:
            try:
                call = ToolCall(id=f"mcp_health_{int(datetime.now(timezone.utc).timestamp()*1000)}", name="get_service_health", arguments={"service_name": service_name})
                result = self.tool_dispatcher.dispatch(call)
                return serialize_tool_result(result)
            except Exception as exc:
                return {"status": "error", "error": str(exc), "evidence_count": 0, "evidence": []}

        @self.mcp.tool(
            name="get_service_dependencies",
            description="Find upstream and downstream microservice dependencies for a specific service.",
        )
        def get_service_dependencies(
            service_name: str = Field(
                ...,
                max_length=100,
                description="The name of the service to find dependencies for",
            ),
        ) -> dict[str, Any]:
            try:
                call = ToolCall(id=f"mcp_deps_{int(datetime.now(timezone.utc).timestamp()*1000)}", name="get_service_dependencies", arguments={"service_name": service_name})
                result = self.tool_dispatcher.dispatch(call)
                return serialize_tool_result(result)
            except Exception as exc:
                return {"status": "error", "error": str(exc), "evidence_count": 0, "evidence": []}

        @self.mcp.tool(
            name="get_evidence",
            description="Retrieve persisted Evidence records from the Aegis investigation repository with full provenance metadata.",
        )
        def get_evidence(
            evidence_id: str | None = Field(
                default=None,
                max_length=100,
                description="Optional specific Evidence ID to look up (e.g. 'ev_m_123', 'ev_trace_456')",
            ),
            incident_id: str | None = Field(
                default=None,
                max_length=100,
                description="Optional Incident ID to fetch all associated evidence items for",
            ),
            service_resource: str | None = Field(
                default=None,
                max_length=100,
                description="Optional service name filter (e.g. 'product-catalog')",
            ),
            evidence_type: str | None = Field(
                default=None,
                max_length=20,
                description="Optional evidence type filter ('METRIC', 'LOG', 'TRACE', 'DEPLOYMENT', 'TOPOLOGY')",
            ),
            limit: int = Field(
                default=50,
                ge=1,
                le=100,
                description="Maximum number of evidence items to return (1-100, default 50)",
            ),
        ) -> dict[str, Any]:
            if not self.repository:
                return {
                    "status": "error",
                    "error": "SQLiteRepository not configured on MCP server.",
                    "evidence_count": 0,
                    "evidence": [],
                }

            try:
                if evidence_id:
                    ev = self.repository.load_evidence(evidence_id)
                    if not ev:
                        return {
                            "status": "not_found",
                            "message": f"Evidence '{evidence_id}' not found in repository.",
                            "evidence_count": 0,
                            "evidence": [],
                        }
                    return {
                        "status": "success",
                        "evidence_count": 1,
                        "evidence": [serialize_evidence(ev)],
                    }

                items = self.repository.list_evidence(
                    incident_id=incident_id,
                    service_resource=service_resource,
                    evidence_type=evidence_type,
                    limit=limit,
                )
                return {
                    "status": "success",
                    "evidence_count": len(items),
                    "evidence": [serialize_evidence(e) for e in items],
                }
            except Exception as exc:
                return {"status": "error", "error": str(exc), "evidence_count": 0, "evidence": []}

        # Error-focused specialized tools
        @self.mcp.tool(
            name="query_error_metrics",
            description="Query specifically for HTTP 5xx error metrics and failed request rates for a service within a 15-minute window.",
        )
        def query_error_metrics(
            service_name: str = Field(
                ...,
                max_length=100,
                description="The name of the service to check error metrics for",
            ),
        ) -> dict[str, Any]:
            try:
                call = ToolCall(id=f"mcp_errmetric_{int(datetime.now(timezone.utc).timestamp()*1000)}", name="query_error_metrics", arguments={"service_name": service_name})
                result = self.tool_dispatcher.dispatch(call)
                return serialize_tool_result(result)
            except Exception as exc:
                return {"status": "error", "error": str(exc), "evidence_count": 0, "evidence": []}

        @self.mcp.tool(
            name="search_error_logs",
            description="Search specifically for ERROR and WARN severity logs from a service within a 15-minute window.",
        )
        def search_error_logs(
            service_name: str = Field(
                ...,
                max_length=100,
                description="The name of the service to search error logs for",
            ),
        ) -> dict[str, Any]:
            try:
                call = ToolCall(id=f"mcp_errlog_{int(datetime.now(timezone.utc).timestamp()*1000)}", name="search_error_logs", arguments={"service_name": service_name})
                result = self.tool_dispatcher.dispatch(call)
                return serialize_tool_result(result)
            except Exception as exc:
                return {"status": "error", "error": str(exc), "evidence_count": 0, "evidence": []}

        @self.mcp.tool(
            name="search_error_traces",
            description="Search for distributed traces containing error status tags from a specific service within a 15-minute window.",
        )
        def search_error_traces(
            service_name: str = Field(
                ...,
                max_length=100,
                description="The name of the service to search error traces for",
            ),
        ) -> dict[str, Any]:
            try:
                call = ToolCall(id=f"mcp_errtrace_{int(datetime.now(timezone.utc).timestamp()*1000)}", name="search_error_traces", arguments={"service_name": service_name})
                result = self.tool_dispatcher.dispatch(call)
                return serialize_tool_result(result)
            except Exception as exc:
                return {"status": "error", "error": str(exc), "evidence_count": 0, "evidence": []}

    def get_mcp_server(self) -> MCPServer:
        """Return the underlying configured MCPServer instance."""
        return self.mcp


def create_mcp_server(
    metrics_provider: MetricsProvider | None = None,
    logs_provider: LogsProvider | None = None,
    topology_provider: TopologyProvider | None = None,
    traces_provider: TracesProvider | None = None,
    tool_dispatcher: ToolDispatcher | None = None,
    repository: SQLiteRepository | None = None,
    name: str = "aegis-investigation-server",
    version: str = "1.1.0",
) -> AegisMCPServer:
    """Factory function to create and configure an AegisMCPServer instance."""
    if not tool_dispatcher:
        if not metrics_provider or not logs_provider or not topology_provider:
            from src.adapters.mock_providers import (
                MockLogsProvider,
                MockMetricsProvider,
                MockTopologyProvider,
                MockTracesProvider,
            )
            metrics_provider = metrics_provider or MockMetricsProvider()
            logs_provider = logs_provider or MockLogsProvider()
            topology_provider = topology_provider or MockTopologyProvider()
            traces_provider = traces_provider or MockTracesProvider()

        tool_dispatcher = ToolDispatcher(
            metrics_provider=metrics_provider,
            logs_provider=logs_provider,
            topology_provider=topology_provider,
            traces_provider=traces_provider,  # type: ignore
            incident_start_time=datetime.now(timezone.utc),
        )

    return AegisMCPServer(
        tool_dispatcher=tool_dispatcher,
        repository=repository,
        name=name,
        version=version,
    )
