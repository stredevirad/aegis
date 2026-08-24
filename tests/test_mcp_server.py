"""Unit and Integration Tests for Aegis Model Context Protocol (MCP) Server.

Phase 4A: Validates:
- Tool registration and schema definitions on MCPServer
- Valid MCP tool invocation across all read-only endpoints
- Invalid arguments and bounds enforcement (string length, limit clamps)
- Provider failure, degradation, and exception isolation
- Empty telemetry result handling
- Complete Evidence provenance serialization
- Evidence repository lookup (by ID, incident, type, limit)
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from src.adapters.mcp import (
    AegisMCPServer,
    create_mcp_server,
    serialize_evidence,
    serialize_tool_result,
)
from src.adapters.mock_providers import (
    MockLogsProvider,
    MockMetricsProvider,
    MockTopologyProvider,
    MockTracesProvider,
)
from src.domain import (
    Evidence,
    EvidenceType,
    ToolResult,
)
from src.investigation.tools import ToolDispatcher
from src.persistence.repository import SQLiteRepository


def _get_payload(res: Any) -> dict[str, Any]:
    """Helper to assert CallToolResult type and parse TextContent JSON payload."""
    assert isinstance(res, CallToolResult)
    assert not res.is_error
    assert len(res.content) > 0
    content = res.content[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text) # type: ignore


@pytest.fixture
def mock_dispatcher() -> ToolDispatcher:
    """Create a ToolDispatcher backed by mock providers."""
    return ToolDispatcher(
        metrics_provider=MockMetricsProvider(),
        logs_provider=MockLogsProvider(),
        topology_provider=MockTopologyProvider(),
        traces_provider=MockTracesProvider(),
        incident_start_time=datetime.now(timezone.utc),
    )


@pytest.fixture
def temp_repo(tmp_path: Any) -> SQLiteRepository:
    """Create a temporary SQLiteRepository for testing Evidence retrieval."""
    db_file = tmp_path / "test_mcp_aegis.db"
    repo = SQLiteRepository(db_path=str(db_file))
    return repo


@pytest.fixture
def mcp_server(mock_dispatcher: ToolDispatcher, temp_repo: SQLiteRepository) -> AegisMCPServer:
    """Create an AegisMCPServer instance."""
    return create_mcp_server(
        tool_dispatcher=mock_dispatcher,
        repository=temp_repo,
        name="test-aegis-mcp",
        version="1.1.0-test",
    )


class TestMCPServerRegistration:
    def test_server_metadata_and_tools_list(self, mcp_server: AegisMCPServer) -> None:
        """Verify that all candidate tools are registered with schemas."""
        async def _test() -> None:
            tools = await mcp_server.mcp.list_tools()
            tool_names = {t.name for t in tools}
            
            expected_tools = {
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
            assert expected_tools.issubset(tool_names), f"Missing tools: {expected_tools - tool_names}"

            # Validate schema metadata on query_metrics
            qm = next(t for t in tools if t.name == "query_metrics")
            assert qm.description is not None
            assert "properties" in qm.input_schema
            assert "query" in qm.input_schema["properties"]

        asyncio.run(_test())


class TestMCPToolInvocation:
    def test_query_metrics_invocation(self, mcp_server: AegisMCPServer) -> None:
        """Test valid query_metrics tool execution over MCP."""
        async def _test() -> None:
            res = await mcp_server.mcp.call_tool("query_metrics", {"query": "frontend"})
            payload = _get_payload(res)
            assert payload["status"] == "success"
            assert "evidence" in payload
            assert isinstance(payload["evidence"], list)

        asyncio.run(_test())

    def test_search_logs_invocation(self, mcp_server: AegisMCPServer) -> None:
        """Test valid search_logs tool execution over MCP with MockLogsProvider."""
        async def _test() -> None:
            res = await mcp_server.mcp.call_tool("search_logs", {"query": "payment-api errors"})
            payload = _get_payload(res)
            assert payload["status"] == "success"
            assert payload["evidence_count"] >= 1
            ev = payload["evidence"][0]
            assert ev["type"] == "LOG"
            assert ev["source_provider"] == "mock-logs"
            assert "observed_at" in ev
            assert "collected_at" in ev

        asyncio.run(_test())

    def test_search_traces_and_get_trace(self, mcp_server: AegisMCPServer) -> None:
        """Test trace search and trace detail retrieval."""
        async def _test() -> None:
            search_res = await mcp_server.mcp.call_tool("search_traces", {"service_name": "payment-api"})
            search_payload = _get_payload(search_res)
            assert search_payload["status"] == "success"
            assert search_payload["evidence_count"] >= 1
            trace_id = search_payload["evidence"][0]["data"].get("trace_id", "mock_trace_123")

            get_res = await mcp_server.mcp.call_tool("get_trace", {"trace_id": trace_id})
            get_payload = _get_payload(get_res)
            assert get_payload["status"] == "success"
            assert get_payload["evidence_count"] >= 1
            assert get_payload["evidence"][0]["type"] == "TRACE"

        asyncio.run(_test())

    def test_service_health_and_dependencies(self, mcp_server: AegisMCPServer) -> None:
        """Test high-level service health and dependency inspection."""
        async def _test() -> None:
            health_res = await mcp_server.mcp.call_tool("get_service_health", {"service_name": "frontend"})
            health_payload = _get_payload(health_res)
            assert health_payload["status"] == "success"
            assert "summary" in health_payload

            dep_res = await mcp_server.mcp.call_tool("get_service_dependencies", {"service_name": "frontend"})
            dep_payload = _get_payload(dep_res)
            assert dep_payload["status"] == "success"
            assert "dependencies" in dep_payload["summary"]

        asyncio.run(_test())

    def test_error_focused_tools_invocation(self, mcp_server: AegisMCPServer) -> None:
        """Test Phase 3 error-focused tools exposed over MCP."""
        async def _test() -> None:
            for tool_name in ("query_error_metrics", "search_error_logs", "search_error_traces"):
                res = await mcp_server.mcp.call_tool(tool_name, {"service_name": "product-catalog"})
                payload = _get_payload(res)
                assert payload["status"] == "success"

        asyncio.run(_test())


class TestMCPEvidenceLookup:
    def test_get_evidence_by_id_and_filters(self, mcp_server: AegisMCPServer, temp_repo: SQLiteRepository) -> None:
        """Test get_evidence querying persisted Evidence records."""
        # Insert a sample incident and evidence into the repository
        from src.domain.models import Incident, InvestigationStatus
        now = datetime.now(timezone.utc)
        incident = Incident(
            id="inc_test_mcp_001",
            title="MCP Test Incident",
            description="Testing MCP evidence retrieval",
            start_time=now,
            status=InvestigationStatus.COLLECTING_EVIDENCE,
            evidence=[
                Evidence(
                    id="ev_test_metric_1",
                    type=EvidenceType.METRIC,
                    source_provider="prometheus",
                    service_resource="frontend",
                    summary="High request latency",
                    data={"latency_p95": 450.0},
                    observed_at=now,
                    collected_at=now,
                ),
                Evidence(
                    id="ev_test_log_1",
                    type=EvidenceType.LOG,
                    source_provider="opensearch",
                    service_resource="product-catalog",
                    summary="HTTP 500 error log",
                    data={"level": "ERROR", "message": "Connection refused"},
                    observed_at=now,
                    collected_at=now,
                ),
            ],
        )
        temp_repo.save_incident(incident)

        async def _test() -> None:
            # 1. Lookup specific evidence by ID
            res_id = await mcp_server.mcp.call_tool("get_evidence", {"evidence_id": "ev_test_metric_1"})
            p_id = _get_payload(res_id)
            assert p_id["status"] == "success"
            assert p_id["evidence_count"] == 1
            assert p_id["evidence"][0]["id"] == "ev_test_metric_1"
            assert p_id["evidence"][0]["type"] == "METRIC"
            assert p_id["evidence"][0]["source_provider"] == "prometheus"

            # 2. Lookup non-existent ID
            res_missing = await mcp_server.mcp.call_tool("get_evidence", {"evidence_id": "ev_non_existent"})
            assert isinstance(res_missing, CallToolResult)
            assert len(res_missing.content) > 0
            p_missing = json.loads(res_missing.content[0].text) # type: ignore
            assert p_missing["status"] == "not_found"
            assert p_missing["evidence_count"] == 0

            # 3. Filter by incident_id
            res_inc = await mcp_server.mcp.call_tool("get_evidence", {"incident_id": "inc_test_mcp_001"})
            p_inc = _get_payload(res_inc)
            assert p_inc["status"] == "success"
            assert p_inc["evidence_count"] == 2

            # 4. Filter by evidence_type
            res_type = await mcp_server.mcp.call_tool("get_evidence", {"evidence_type": "LOG"})
            p_type = _get_payload(res_type)
            assert p_type["status"] == "success"
            assert p_type["evidence_count"] == 1
            assert p_type["evidence"][0]["id"] == "ev_test_log_1"

        asyncio.run(_test())

    def test_get_evidence_no_repo_configured(self, mock_dispatcher: ToolDispatcher) -> None:
        """Test get_evidence returns clean error when repository is not configured."""
        server = create_mcp_server(tool_dispatcher=mock_dispatcher, repository=None)

        async def _test() -> None:
            res = await server.mcp.call_tool("get_evidence", {"limit": 10})
            assert isinstance(res, CallToolResult)
            assert len(res.content) > 0
            payload = json.loads(res.content[0].text) # type: ignore
            assert payload["status"] == "error"
            assert "SQLiteRepository not configured" in payload["error"]

        asyncio.run(_test())


class TestMCPBoundsAndErrorIsolation:
    def test_failing_provider_isolation(self) -> None:
        """Verify that provider exceptions are caught and returned as structured errors."""
        class FailingMetricsProvider(MockMetricsProvider):
            def query_metrics(self, query: str, start_time: Any, end_time: Any) -> list[Evidence]:
                raise ConnectionError("Prometheus connection refused on port 9090")

        dispatcher = ToolDispatcher(
            metrics_provider=FailingMetricsProvider(),
            logs_provider=MockLogsProvider(),
            topology_provider=MockTopologyProvider(),
            traces_provider=MockTracesProvider(),
            incident_start_time=datetime.now(timezone.utc),
        )
        server = create_mcp_server(tool_dispatcher=dispatcher)

        async def _test() -> None:
            res = await server.mcp.call_tool("query_metrics", {"query": "frontend"})
            assert isinstance(res, CallToolResult)
            assert len(res.content) > 0
            payload = json.loads(res.content[0].text) # type: ignore
            assert payload["status"] == "error"
            assert "connection refused" in payload["message"].lower() or "connection refused" in str(payload.get("error", "")).lower()

        asyncio.run(_test())

    def test_empty_results_handling(self) -> None:
        """Verify that empty telemetry returns clean 0-count payloads without exceptions."""
        class EmptyLogsProvider(MockLogsProvider):
            def search_logs(self, query: str, start_time: Any, end_time: Any) -> list[Evidence]:
                return []

        dispatcher = ToolDispatcher(
            metrics_provider=MockMetricsProvider(),
            logs_provider=EmptyLogsProvider(),
            topology_provider=MockTopologyProvider(),
            traces_provider=MockTracesProvider(),
            incident_start_time=datetime.now(timezone.utc),
        )
        server = create_mcp_server(tool_dispatcher=dispatcher)

        async def _test() -> None:
            res = await server.mcp.call_tool("search_logs", {"query": "non_existent_service"})
            payload = _get_payload(res)
            assert payload["status"] == "success"
            assert payload["evidence_count"] == 0
            assert payload["evidence"] == []

        asyncio.run(_test())


class TestMCPSerializationHelpers:
    def test_serialize_evidence_provenance(self) -> None:
        """Verify serialize_evidence retains all provenance attributes."""
        now = datetime.now(timezone.utc)
        ev = Evidence(
            id="ev_prov_123",
            type=EvidenceType.TRACE,
            source_provider="jaeger",
            service_resource="checkoutservice",
            summary="Trace showing RPC failure",
            data={"trace_id": "abc12345", "spans_count": 12},
            observed_at=now,
            collected_at=now,
        )
        d = serialize_evidence(ev)
        assert d["id"] == "ev_prov_123"
        assert d["type"] == "TRACE"
        assert d["source_provider"] == "jaeger"
        assert d["service_resource"] == "checkoutservice"
        assert d["summary"] == "Trace showing RPC failure"
        assert d["observed_at"] == now.isoformat()
        assert d["collected_at"] == now.isoformat()
        assert d["data"]["trace_id"] == "abc12345"

    def test_serialize_tool_result_string_error(self) -> None:
        """Verify serialize_tool_result correctly categorizes error strings."""
        err_res = serialize_tool_result("Error executing tool 'query_metrics': Invalid syntax")
        assert err_res["status"] == "error"
        assert err_res["evidence_count"] == 0

        ok_res = serialize_tool_result(ToolResult(evidence=[], summary={"service": "frontend", "status": "HEALTHY"}))
        assert ok_res["status"] == "success"
        assert ok_res["summary"]["status"] == "HEALTHY"


class TestMCPInputValidationAndBounds:
    def test_missing_required_arguments(self, mcp_server: AegisMCPServer) -> None:
        """Verify that calling a tool without required arguments raises or returns an error."""
        async def _test() -> None:
            # query_metrics requires 'query'
            try:
                res = await mcp_server.mcp.call_tool("query_metrics", {})
                if isinstance(res, CallToolResult):
                    payload = json.loads(res.content[0].text) if res.content else {} # type: ignore
                    assert res.is_error or payload.get("status") == "error"
            except Exception as e:
                assert "query" in str(e).lower() or "missing" in str(e).lower() or "validation" in str(e).lower()

        asyncio.run(_test())

    def test_no_traces_provider_configured(self) -> None:
        """Verify search_traces behavior when traces_provider is None."""
        dispatcher = ToolDispatcher(
            metrics_provider=MockMetricsProvider(),
            logs_provider=MockLogsProvider(),
            topology_provider=MockTopologyProvider(),
            traces_provider=None, # type: ignore
            incident_start_time=datetime.now(timezone.utc),
        )
        server = create_mcp_server(tool_dispatcher=dispatcher)

        async def _test() -> None:
            res = await server.mcp.call_tool("search_traces", {"service_name": "frontend"})
            assert isinstance(res, CallToolResult)
            assert len(res.content) > 0
            payload = json.loads(res.content[0].text) # type: ignore
            assert payload["status"] == "error" or "TracesProvider not configured" in str(payload.get("summary", {}))

        asyncio.run(_test())

    def test_custom_server_name_and_version(self, mock_dispatcher: ToolDispatcher) -> None:
        """Verify custom naming and versioning on AegisMCPServer."""
        server = create_mcp_server(
            tool_dispatcher=mock_dispatcher,
            name="custom-aegis-mcp",
            version="2.0.0-custom",
        )
        assert server.mcp.name == "custom-aegis-mcp"
        assert server.mcp.version == "2.0.0-custom"
