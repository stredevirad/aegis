"""Unit and Integration Tests for Aegis MCP Client Adapter.

Phase 4B: Validates discovery, schema mapping, allowlisting, invocation,
dispatch integration, timeout bounds, and Evidence reconstruction.
"""
import asyncio
import sys
from datetime import datetime, timezone
from typing import Any

from src.adapters.mcp import AegisMCPClient
from src.adapters.mock_providers import (
    MockLogsProvider,
    MockMetricsProvider,
    MockTopologyProvider,
    MockTracesProvider,
)
from src.domain import EvidenceType, ToolCall, ToolResult
from src.investigation.tools import ToolDispatcher


class TestAegisMCPClientDiscoveryAndAllowlist:
    """Test remote tool discovery, schema mapping, and allowlisting."""

    def test_discover_all_allowed_tools(self) -> None:
        """Verify discovery returns all tools when allowlist matches server tools."""
        client = AegisMCPClient(
            server_command=sys.executable,
            server_args=["-m", "src.adapters.mcp"],
            allowed_tools={"query_metrics", "search_logs", "get_service_health"},
        )

        async def _test() -> None:
            defs = await client.discover_tools()
            tool_names = {d.name for d in defs}
            assert tool_names == {"query_metrics", "search_logs", "get_service_health"}

            # Validate mapped schema on query_metrics
            qm_def = next(d for d in defs if d.name == "query_metrics")
            assert qm_def.description is not None
            assert "query" in qm_def.parameters
            assert qm_def.required == ["query"]

        asyncio.run(_test())

    def test_allowlist_filters_unapproved_tools(self) -> None:
        """Verify client filters out tools not included in the allowlist."""
        client = AegisMCPClient(
            server_command=sys.executable,
            server_args=["-m", "src.adapters.mcp"],
            allowed_tools={"get_service_health"},  # Only 1 tool allowed
        )

        async def _test() -> None:
            defs = await client.discover_tools()
            assert len(defs) == 1
            assert defs[0].name == "get_service_health"

        asyncio.run(_test())


class TestAegisMCPClientInvocation:
    """Test invoking external MCP tools through AegisMCPClient."""

    def test_invoke_allowed_tool_with_evidence_reconstruction(self) -> None:
        """Verify invoking query_metrics reconstructs Evidence items with provenance."""
        client = AegisMCPClient(
            server_command=sys.executable,
            server_args=["-m", "src.adapters.mcp"],
            server_name="test-server",
            allowed_tools={"query_metrics"},
        )

        async def _test() -> None:
            result = await client.invoke_tool("query_metrics", {"query": "payment-api latency"})
            assert isinstance(result, ToolResult)
            assert len(result.evidence) >= 1
            ev = result.evidence[0]
            assert ev.type == EvidenceType.METRIC
            assert ev.source_provider == "mock-metrics"
            assert ev.service_resource == "payment-api"
            assert ev.observed_at is not None
            assert ev.collected_at is not None

        asyncio.run(_test())

    def test_invoke_unapproved_tool_is_blocked(self) -> None:
        """Verify calling a tool not in the allowlist returns a blocked ToolResult."""
        client = AegisMCPClient(
            server_command=sys.executable,
            server_args=["-m", "src.adapters.mcp"],
            allowed_tools={"query_metrics"},  # search_logs is not allowed
        )

        async def _test() -> None:
            result = await client.invoke_tool("search_logs", {"query": "payment-api errors"})
            assert isinstance(result, ToolResult)
            assert isinstance(result.summary, dict)
            assert result.summary.get("status") == "blocked"
            assert "allowlist" in str(result.summary.get("error", "")).lower()

        asyncio.run(_test())

    def test_invoke_with_oversized_payload_rejected(self) -> None:
        """Verify client rejects payload exceeding maximum permitted size."""
        client = AegisMCPClient(
            server_command=sys.executable,
            server_args=["-m", "src.adapters.mcp"],
            allowed_tools={"query_metrics"},
        )

        async def _test() -> None:
            oversized_args = {"query": "x" * 15000}
            result = await client.invoke_tool("query_metrics", oversized_args)
            assert isinstance(result, ToolResult)
            assert isinstance(result.summary, dict)
            assert result.summary.get("status") == "error"
            assert "exceeded maximum permitted size" in str(result.summary.get("error", ""))

        asyncio.run(_test())


class TestAegisMCPClientDispatcherIntegration:
    """Test bridging AegisMCPClient tools into ToolDispatcher."""

    def test_register_mcp_tool_with_dispatcher(self) -> None:
        """Verify external MCP tool can be registered and dispatched through ToolDispatcher."""
        client = AegisMCPClient(
            server_command=sys.executable,
            server_args=["-m", "src.adapters.mcp"],
            allowed_tools={"get_service_dependencies"},
        )

        dispatcher = ToolDispatcher(
            metrics_provider=MockMetricsProvider(),
            logs_provider=MockLogsProvider(),
            topology_provider=MockTopologyProvider(),
            traces_provider=MockTracesProvider(),
            incident_start_time=datetime.now(timezone.utc),
        )

        # 1. Discover tools asynchronously
        defs = asyncio.run(client.discover_tools())
        assert len(defs) == 1
        dep_def = defs[0]

        # 2. Create dynamic args model
        args_model = client.create_pydantic_args_model(dep_def)

        # 3. Handler bridge executing synchronous dispatch
        def _bridge_handler(**kwargs: Any) -> ToolResult:
            return asyncio.run(client.invoke_tool(dep_def.name, kwargs))

        # 4. Register with dispatcher
        dispatcher.register_custom_tool(
            definition=dep_def,
            handler=_bridge_handler,
            args_model=args_model,
        )

        # 5. Confirm definition is listed in dispatcher
        all_defs = dispatcher.get_tool_definitions()
        assert any(d.name == "get_service_dependencies" for d in all_defs)

        # 6. Dispatch tool call through ToolDispatcher
        call = ToolCall(
            id="call_mcp_dep_1",
            name="get_service_dependencies",
            arguments={"service_name": "frontend"},
        )
        dispatch_res = dispatcher.dispatch(call)
        assert isinstance(dispatch_res, ToolResult)
        assert isinstance(dispatch_res.summary, dict)
        assert "dependencies" in dispatch_res.summary

    def test_dispatcher_duplicate_registration_deduplication(self) -> None:
        """Verify registering a tool with the same name replaces definition without duplicating."""
        dispatcher = ToolDispatcher(
            metrics_provider=MockMetricsProvider(),
            logs_provider=MockLogsProvider(),
            topology_provider=MockTopologyProvider(),
            traces_provider=MockTracesProvider(),
            incident_start_time=datetime.now(timezone.utc),
        )
        from src.domain import ToolDefinition

        custom_def_1 = ToolDefinition(name="custom_mcp_tool", description="V1", parameters={}, required=[])
        custom_def_2 = ToolDefinition(name="custom_mcp_tool", description="V2", parameters={}, required=[])

        dispatcher.register_custom_tool(custom_def_1, lambda: ToolResult(summary="v1"))
        dispatcher.register_custom_tool(custom_def_2, lambda: ToolResult(summary="v2"))

        all_defs = dispatcher.get_tool_definitions()
        matching = [d for d in all_defs if d.name == "custom_mcp_tool"]
        assert len(matching) == 1
        assert matching[0].description == "V2"


class TestAegisMCPClientEdgeCases:
    """Test timeout bounds and dynamic Pydantic schema generation."""

    def test_client_timeout_handling(self) -> None:
        """Verify client respects small timeout bounds without hanging."""
        # Set ultra-low timeout (0.0001s) to force timeout branch
        client = AegisMCPClient(
            server_command=sys.executable,
            server_args=["-m", "src.adapters.mcp"],
            allowed_tools={"query_metrics"},
            timeout_seconds=0.0001,
        )

        async def _test() -> None:
            res = await client.invoke_tool("query_metrics", {"query": "payment-api latency"})
            assert isinstance(res, ToolResult)
            assert isinstance(res.summary, dict)
            assert res.summary.get("status") == "error"
            assert "timed out" in str(res.summary.get("error", "")).lower() or "failed" in str(res.summary.get("error", "")).lower()

        asyncio.run(_test())

    def test_dynamic_schema_generation_types(self) -> None:
        """Verify dynamic schema model maps integers, booleans, floats, and strings."""
        client = AegisMCPClient()
        from src.domain import ToolDefinition

        sample_def = ToolDefinition(
            name="sample_complex_tool",
            description="Test types",
            parameters={
                "str_param": {"type": "string"},
                "int_param": {"type": "integer"},
                "bool_param": {"type": "boolean"},
                "float_param": {"type": "number"},
            },
            required=["str_param", "int_param"],
        )
        model = client.create_pydantic_args_model(sample_def)
        inst = model(str_param="hello", int_param=42, bool_param=True, float_param=3.14)
        assert inst.str_param == "hello" # type: ignore
        assert inst.int_param == 42 # type: ignore
        assert inst.bool_param is True # type: ignore
        assert inst.float_param == 3.14 # type: ignore

