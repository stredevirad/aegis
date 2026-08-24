"""Integration and Policy Tests for OpenTelemetry Demo MCP Service Integration.

Phase 4C: Validates read-only tool allowlisting, mutation blocking,
Evidence provenance tagging ('otel-demo-mcp'), and graceful degradation.
"""
import asyncio
from typing import Any

import httpx
import pytest

from src.adapters.mcp import (
    OTEL_DEMO_MUTATING_TOOLS,
    OTEL_DEMO_READ_ONLY_TOOLS,
    AegisMCPClient,
    OTelDemoMCPAdapter,
)
from src.domain import Evidence, EvidenceType, ToolDefinition, ToolResult


class MockOTelMCPClient(AegisMCPClient):
    """Mock client simulating OpenTelemetry Demo Astronomy Shop FastMCP server."""

    def __init__(self) -> None:
        super().__init__(server_name="otel-demo-mcp", allowed_tools=OTEL_DEMO_READ_ONLY_TOOLS)

    async def discover_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="list_products",
                description="List products in Astronomy Shop catalog",
                parameters={},
                required=[],
            ),
            ToolDefinition(
                name="get_product",
                description="Get details for a specific product ID",
                parameters={"id": {"type": "string"}},
                required=["id"],
            ),
            ToolDefinition(
                name="checkout",
                description="Place order (MUTATING)",
                parameters={"cart_id": {"type": "string"}},
                required=["cart_id"],
            ),
        ]

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if tool_name == "list_products":
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            return ToolResult(
                evidence=[
                    Evidence(
                        id="ev_prod_001",
                        type=EvidenceType.TOPOLOGY,
                        source_provider="otel-demo-mcp",
                        service_resource="productcatalogservice",
                        summary="Catalog item: Stargazer Telescope ($499.00)",
                        data={"product_id": "OLJCESPC7Z", "name": "Stargazer Telescope", "price_usd": 499.0},
                        observed_at=now,
                        collected_at=now,
                    )
                ],
                summary={"products_count": 1},
            )
        elif tool_name == "get_product":
            return ToolResult(
                summary={"id": arguments.get("id"), "name": "National Park Foundation Solar Filter"}
            )
        elif tool_name in OTEL_DEMO_MUTATING_TOOLS:
            return ToolResult(
                summary={"status": "blocked", "error": "Mutating tools are not permitted."}
            )
        return ToolResult(summary={"status": "not_found"})


class TestOTelDemoMCPPolicy:
    """Test policy boundaries, allowlisting, and mutation blocking."""

    def test_read_only_allowlist_structure(self) -> None:
        """Verify defined constants clearly separate read-only vs mutating tools."""
        assert "list_products" in OTEL_DEMO_READ_ONLY_TOOLS
        assert "get_product" in OTEL_DEMO_READ_ONLY_TOOLS
        assert "get_recommendations" in OTEL_DEMO_READ_ONLY_TOOLS
        assert "checkout" in OTEL_DEMO_MUTATING_TOOLS
        assert "add_to_cart" in OTEL_DEMO_MUTATING_TOOLS
        assert "empty_cart" in OTEL_DEMO_MUTATING_TOOLS

        # Assert no overlap
        assert OTEL_DEMO_READ_ONLY_TOOLS.isdisjoint(OTEL_DEMO_MUTATING_TOOLS)

    def test_mutating_tools_blocked_by_adapter(self) -> None:
        """Verify calling a mutating tool is blocked immediately at adapter level."""
        adapter = OTelDemoMCPAdapter(mcp_client=MockOTelMCPClient())

        async def _test() -> None:
            for tool_name in ("checkout", "add_to_cart", "empty_cart"):
                res = await adapter.invoke_context_tool(tool_name, {"cart_id": "cart_123"})
                assert isinstance(res, ToolResult)
                assert isinstance(res.summary, dict)
                assert res.summary.get("status") == "blocked"
                assert "mutating" in str(res.summary.get("error", "")).lower()

        asyncio.run(_test())

    def test_read_only_discovery_and_provenance(self) -> None:
        """Verify read-only tools are discovered and Evidence is tagged with otel-demo-mcp."""
        adapter = OTelDemoMCPAdapter(mcp_client=MockOTelMCPClient())

        async def _test() -> None:
            # Discovery should only include read-only tools
            tools = await adapter.discover_context_tools()
            tool_names = {t.name for t in tools}
            assert "list_products" in tool_names
            assert "get_product" in tool_names
            assert "checkout" not in tool_names

            # Invocation should return Evidence with provenance 'otel-demo-mcp'
            res = await adapter.invoke_context_tool("list_products", {})
            assert len(res.evidence) == 1
            ev = res.evidence[0]
            assert ev.source_provider == "otel-demo-mcp"
            assert ev.source_provider not in ("prometheus", "opensearch", "jaeger")
            assert ev.service_resource == "productcatalogservice"

        asyncio.run(_test())

    @pytest.mark.integration
    def test_live_otel_mcp_service_connection(self) -> None:
        """Test live HTTP stream transport connection to OTel Demo MCP if running on port 8011."""
        try:
            r = httpx.get("http://localhost:8011/mcp", timeout=1.0)
            if r.status_code not in (200, 405):  # MCP endpoint may return 200 or 405 for GET
                pytest.skip("OTel Demo MCP service on :8011 not ready")
        except Exception:
            pytest.skip("OTel Demo MCP service (compose.agent.yaml) not running on port 8011")

        # If reachable, execute live test
        adapter = OTelDemoMCPAdapter(endpoint_url="http://localhost:8011/mcp")

        async def _test() -> None:
            tools = await adapter.discover_context_tools()
            assert isinstance(tools, list)

        asyncio.run(_test())
