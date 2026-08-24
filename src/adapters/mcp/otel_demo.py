"""OpenTelemetry Demo MCP Service Integration Adapter.

Phase 4C: Integrates with the OpenTelemetry Demo Astronomy Shop MCP service
(astronomy-shop-mcp) to provide application-level context tools while enforcing
strict read-only allowlisting, domain isolation, and distinct Evidence provenance.
"""
from typing import Any

from src.adapters.mcp.client import AegisMCPClient
from src.domain import ToolDefinition, ToolResult

# Explicit allowlist of READ-ONLY application context tools in OTel Demo
OTEL_DEMO_READ_ONLY_TOOLS = {
    "list_products",
    "get_product",
    "get_recommendations",
    "get_supported_currencies",
    "get_shipping_quote",
    "get_ads",
}

# Mutation/remediation tools that are strictly BLOCKED from automated execution
OTEL_DEMO_MUTATING_TOOLS = {
    "add_to_cart",
    "checkout",
    "empty_cart",
}


class OTelDemoMCPAdapter:
    """Adapter for connecting Aegis to the OpenTelemetry Demo MCP service.
    
    Architectural Boundaries:
    - Telemetry (metrics, logs, traces) remains strictly with Prometheus,
      OpenSearch, and Jaeger providers.
    - OTel Demo MCP tools provide application/product context (e.g. catalog info,
      pricing, recommendations) and are labeled with source_provider='otel-demo-mcp'.
    - Only read-only tools are exposed; mutating tools (e.g. checkout, add_to_cart)
      are strictly rejected.
    """

    def __init__(
        self,
        endpoint_url: str = "http://localhost:8011/mcp",
        allowed_tools: set[str] | None = None,
        timeout_seconds: float = 10.0,
        mcp_client: AegisMCPClient | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.allowed_tools = allowed_tools or set(OTEL_DEMO_READ_ONLY_TOOLS)
        # Ensure no mutating tools can be accidentally allowed
        self.allowed_tools = self.allowed_tools - OTEL_DEMO_MUTATING_TOOLS
        self.timeout_seconds = timeout_seconds
        self.mcp_client = mcp_client or AegisMCPClient(
            server_name="otel-demo-mcp",
            allowed_tools=self.allowed_tools,
            timeout_seconds=self.timeout_seconds,
        )

    async def discover_context_tools(self) -> list[ToolDefinition]:
        """Discover read-only application context tools from OTel Demo MCP."""
        discovered = await self.mcp_client.discover_tools()
        # Filter strictly by read-only allowlist
        return [t for t in discovered if t.name in self.allowed_tools]

    async def invoke_context_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Invoke an approved read-only OTel Demo context tool."""
        if tool_name in OTEL_DEMO_MUTATING_TOOLS:
            return ToolResult(
                summary={
                    "status": "blocked",
                    "error": f"Tool '{tool_name}' is a mutating action and is rejected by Aegis read-only policy.",
                }
            )

        if tool_name not in self.allowed_tools:
            return ToolResult(
                summary={
                    "status": "blocked",
                    "error": f"Tool '{tool_name}' is not in the approved OTel Demo MCP allowlist.",
                }
            )

        result = await self.mcp_client.invoke_tool(tool_name, arguments)
        # Guarantee distinct provenance for any returned Evidence items
        for ev in result.evidence:
            if not ev.source_provider or ev.source_provider.startswith("mcp"):
                ev.source_provider = "otel-demo-mcp"

        return result
