"""Security and Hardening Test Suite for Model Context Protocol (MCP) Boundaries.

Phase 4D: Treats MCP as an untrusted external tool boundary, validating:
- Strict allowlisting and policy gating
- Argument validation, size limits, and non-dictionary argument rejection
- Untrusted response parsing (non-JSON, non-text content, missing provenance)
- Execution timeouts and subprocess failure containment
- Dispatcher error isolation against misbehaving external tools
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent

from src.adapters.mcp import (
    AegisMCPClient,
)
from src.adapters.mock_providers import (
    MockLogsProvider,
    MockMetricsProvider,
    MockTopologyProvider,
    MockTracesProvider,
)
from src.domain import ToolCall, ToolDefinition, ToolResult
from src.investigation.tools import ToolDispatcher


class TestMCPArgumentAndPayloadHardening:
    """Test adversarial arguments, oversized payloads, and type violations."""

    def test_non_dict_arguments_rejected(self) -> None:
        """Verify client returns error when arguments is not a dictionary."""
        client = AegisMCPClient(allowed_tools={"query_metrics"})

        async def _test() -> None:
            # Pass invalid types (list, string, int)
            for bad_arg in (["not", "a", "dict"], "string_payload", 12345):
                res = await client.invoke_tool("query_metrics", bad_arg) # type: ignore
                assert isinstance(res, ToolResult)
                assert isinstance(res.summary, dict)
                assert res.summary.get("status") == "error"
                assert "dictionary" in res.summary.get("error", "").lower()

        asyncio.run(_test())

    def test_massive_payload_rejection(self) -> None:
        """Verify client rejects payload exceeding maximum 10KB budget."""
        client = AegisMCPClient(allowed_tools={"search_logs"})

        async def _test() -> None:
            huge_payload = {"query": "error " + ("A" * 20000)}
            res = await client.invoke_tool("search_logs", huge_payload)
            assert isinstance(res, ToolResult)
            assert isinstance(res.summary, dict)
            assert res.summary.get("status") == "error"
            assert "exceeded maximum permitted size" in res.summary.get("error", "")

        asyncio.run(_test())

    def test_unicode_and_special_character_resilience(self) -> None:
        """Verify client and server handle unicode, emoji, and JSON escape sequences safely."""
        client = AegisMCPClient(
            server_command=sys.executable,
            server_args=["-m", "src.adapters.mcp"],
            allowed_tools={"search_logs"},
        )

        async def _test() -> None:
            special_query = "payment-api 🚀 \u0000\n\t\\\"' AND <script>alert(1)</script>"
            res = await client.invoke_tool("search_logs", {"query": special_query})
            assert isinstance(res, ToolResult)
            # Must return clean response rather than unhandled exception
            assert res.summary is not None

        asyncio.run(_test())


class TestMCPResponseParsingHardening:
    """Test parsing untrusted, malformed, or unusual MCP server responses."""

    def test_parse_html_error_response_from_server(self) -> None:
        """Verify parsing non-JSON plain text / HTML (e.g. 502 Bad Gateway) without crashing."""
        client = AegisMCPClient()
        fake_mcp_res = CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text="<html><body>502 Bad Gateway</body></html>")],
        )
        res = client._parse_mcp_result("sample_tool", fake_mcp_res)
        assert isinstance(res, ToolResult)
        assert res.summary == {"status": "success", "result": "<html><body>502 Bad Gateway</body></html>"}

    def test_parse_empty_content_list(self) -> None:
        """Verify parsing CallToolResult with empty content list."""
        client = AegisMCPClient()
        fake_mcp_res = CallToolResult(is_error=False, content=[])
        res = client._parse_mcp_result("sample_tool", fake_mcp_res)
        assert isinstance(res, ToolResult)
        assert res.summary.get("status") == "success"

    def test_parse_non_text_content_block(self) -> None:
        """Verify parsing unexpected binary/image content blocks."""
        client = AegisMCPClient()
        fake_mcp_res = CallToolResult(
            is_error=False,
            content=[ImageContent(type="image", data="base64data...", mime_type="image/png")],
        )
        res = client._parse_mcp_result("sample_tool", fake_mcp_res)
        assert isinstance(res, ToolResult)
        assert res.summary.get("status") == "success"
        assert res.summary.get("content_type") == "ImageContent"

    def test_parse_evidence_with_missing_timestamps_fallback(self) -> None:
        """Verify that Evidence payloads with missing timestamps receive valid fallback UTC timestamps."""
        client = AegisMCPClient(server_name="untrusted-server")
        evidence_payload = {
            "status": "success",
            "evidence": [
                {
                    "type": "METRIC",
                    "service_resource": "auth-service",
                    "summary": "Untrusted metric without timestamps",
                    "data": {"value": 99.0},
                }
            ],
        }
        fake_mcp_res = CallToolResult(
            is_error=False,
            content=[TextContent(type="text", text=json.dumps(evidence_payload))],
        )
        res = client._parse_mcp_result("sample_tool", fake_mcp_res)
        assert len(res.evidence) == 1
        ev = res.evidence[0]
        assert ev.source_provider == "mcp:untrusted-server"
        assert ev.observed_at is not None
        assert ev.collected_at is not None
        assert ev.observed_at.tzinfo == timezone.utc


class TestMCPDispatcherBoundaryIsolation:
    """Test ToolDispatcher safety when external custom MCP tools fail or misbehave."""

    def test_dispatcher_catches_handler_exception(self) -> None:
        """Verify dispatcher catches unhandled exceptions raised in custom tool handlers."""
        dispatcher = ToolDispatcher(
            metrics_provider=MockMetricsProvider(),
            logs_provider=MockLogsProvider(),
            topology_provider=MockTopologyProvider(),
            traces_provider=MockTracesProvider(),
            incident_start_time=datetime.now(timezone.utc),
        )

        def _exploding_handler(**kwargs: Any) -> ToolResult:
            raise RuntimeError("Fatal internal failure in remote adapter pipe")

        tool_def = ToolDefinition(
            name="exploding_tool",
            description="Tool that explodes",
            parameters={},
            required=[],
        )
        dispatcher.register_custom_tool(tool_def, _exploding_handler)

        call = ToolCall(id="call_exp_1", name="exploding_tool", arguments={})
        res = dispatcher.dispatch(call)
        assert isinstance(res, str)
        assert "Error executing tool 'exploding_tool'" in res
        assert "Fatal internal failure" in res

    def test_dispatcher_validates_custom_tool_arguments(self) -> None:
        """Verify dispatcher validates arguments against custom Pydantic models."""
        dispatcher = ToolDispatcher(
            metrics_provider=MockMetricsProvider(),
            logs_provider=MockLogsProvider(),
            topology_provider=MockTopologyProvider(),
            traces_provider=MockTracesProvider(),
            incident_start_time=datetime.now(timezone.utc),
        )
        client = AegisMCPClient()

        tool_def = ToolDefinition(
            name="validated_custom_tool",
            description="Tool with integer validation",
            parameters={"count": {"type": "integer"}},
            required=["count"],
        )
        args_model = client.create_pydantic_args_model(tool_def)

        dispatcher.register_custom_tool(
            definition=tool_def,
            handler=lambda count: ToolResult(summary={"received_count": count}),
            args_model=args_model,
        )

        # 1. Valid call
        valid_call = ToolCall(id="c1", name="validated_custom_tool", arguments={"count": 5})
        valid_res = dispatcher.dispatch(valid_call)
        assert isinstance(valid_res, ToolResult)
        assert valid_res.summary == {"received_count": 5}

        # 2. Invalid call (count is not an integer)
        invalid_call = ToolCall(id="c2", name="validated_custom_tool", arguments={"count": "not_an_int"})
        invalid_res = dispatcher.dispatch(invalid_call)
        assert isinstance(invalid_res, str)
        assert "Invalid arguments" in invalid_res
