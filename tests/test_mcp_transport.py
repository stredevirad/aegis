"""Integration Tests for Aegis MCP Server over Real Stdio Subprocess Transport.

Phase 4A.1: Validates that Aegis MCP Server operates correctly through
an actual out-of-process stdio transport with the official MCP Python SDK client.
"""
import asyncio
import json
import sys
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, TextContent


def _get_payload(res: Any) -> dict[str, Any]:
    """Helper to assert CallToolResult and extract parsed JSON payload."""
    assert isinstance(res, CallToolResult)
    assert not res.is_error
    assert len(res.content) > 0
    content = res.content[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text) # type: ignore


class TestMCPStdioTransport:
    """Test suite executing MCP tools through real subprocess stdio pipes."""

    def test_stdio_handshake_and_tool_discovery(self) -> None:
        """Verify client initializes session and discovers all registered tools over stdio."""
        async def _test() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "src.adapters.mcp"],
                env=None,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    init_res = await session.initialize()
                    assert init_res is not None
                    assert init_res.server_info.name == "aegis-investigation-server"

                    tools_res = await session.list_tools()
                    discovered = {t.name for t in tools_res.tools}
                    expected = {
                        "query_metrics",
                        "search_logs",
                        "search_traces",
                        "get_trace",
                        "get_service_health",
                        "get_service_dependencies",
                        "get_evidence",
                        "query_error_metrics",
                        "search_error_logs",
                        "search_error_traces",
                    }
                    assert expected.issubset(discovered)

        asyncio.run(_test())

    def test_stdio_representative_tool_calls(self) -> None:
        """Test invoking metrics, logs, traces, and health tools over stdio."""
        async def _test() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "src.adapters.mcp"],
                env=None,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    # 1. query_metrics
                    qm_res = await session.call_tool("query_metrics", {"query": "payment-api latency"})
                    qm_data = _get_payload(qm_res)
                    assert qm_data["status"] == "success"
                    assert qm_data["evidence_count"] >= 1
                    ev_metric = qm_data["evidence"][0]
                    assert ev_metric["type"] == "METRIC"
                    assert ev_metric["source_provider"] == "mock-metrics"
                    assert "observed_at" in ev_metric

                    # 2. search_logs
                    log_res = await session.call_tool("search_logs", {"query": "payment-api errors"})
                    log_data = _get_payload(log_res)
                    assert log_data["status"] == "success"
                    assert log_data["evidence_count"] >= 1
                    ev_log = log_data["evidence"][0]
                    assert ev_log["type"] == "LOG"
                    assert ev_log["source_provider"] == "mock-logs"

                    # 3. search_traces
                    trace_res = await session.call_tool("search_traces", {"service_name": "payment-api"})
                    trace_data = _get_payload(trace_res)
                    assert trace_data["status"] == "success"
                    assert trace_data["evidence_count"] >= 1
                    assert trace_data["evidence"][0]["type"] == "TRACE"

                    # 4. get_trace
                    gt_res = await session.call_tool("get_trace", {"trace_id": "mock_trace_123"})
                    gt_data = _get_payload(gt_res)
                    assert gt_data["status"] == "success"
                    assert gt_data["evidence_count"] >= 1

                    # 5. get_service_health
                    health_res = await session.call_tool("get_service_health", {"service_name": "payment-api"})
                    health_data = _get_payload(health_res)
                    assert health_data["status"] == "success"

                    # 6. query_error_metrics
                    err_res = await session.call_tool("query_error_metrics", {"service_name": "product-catalog"})
                    err_data = _get_payload(err_res)
                    assert err_data["status"] == "success"

        asyncio.run(_test())

    def test_stdio_error_and_bounds_handling(self) -> None:
        """Verify error handling and missing argument validation over stdio transport."""
        async def _test() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "src.adapters.mcp"],
                env=None,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    # Missing required argument should return error
                    try:
                        res = await session.call_tool("query_metrics", {})
                        if isinstance(res, CallToolResult):
                            assert res.is_error
                            assert len(res.content) > 0
                            content = res.content[0]
                            assert isinstance(content, TextContent)
                            err_msg = content.text
                            assert "query" in err_msg.lower() or "missing" in err_msg.lower() or "validation" in err_msg.lower()
                    except Exception as e:
                        assert "query" in str(e).lower() or "validation" in str(e).lower() or "missing" in str(e).lower()

        asyncio.run(_test())

    @pytest.mark.integration
    def test_stdio_live_telemetry_query(self) -> None:
        """Test invoking MCP server over stdio with --live-telemetry against live Prometheus if up."""
        # Check if Prometheus is reachable on localhost:9090
        try:
            r = httpx.get("http://localhost:9090/-/healthy", timeout=2.0)
            if r.status_code != 200:
                pytest.skip("Prometheus not healthy on :9090")
        except Exception:
            pytest.skip("Prometheus not reachable on :9090")

        async def _test() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "src.adapters.mcp", "--live-telemetry"],
                env=None,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    res = await session.call_tool("query_metrics", {"query": "frontend"})
                    payload = _get_payload(res)
                    assert payload["status"] == "success"
                    # Real prometheus returns list of metrics
                    assert isinstance(payload["evidence"], list)

        asyncio.run(_test())
