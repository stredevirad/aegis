"""Tests for Phase 3.2 error-focused evidence querying tools in ToolDispatcher."""
from datetime import datetime, timezone

import pytest
from pydantic import AwareDatetime

from src.adapters.mock_providers import (
    MockLogsProvider,
    MockMetricsProvider,
    MockTopologyProvider,
    MockTracesProvider,
)
from src.domain import (
    Evidence,
    EvidenceType,
    ToolCall,
    ToolResult,
)
from src.investigation.tools import ToolDispatcher


class CustomMockMetricsProvider(MockMetricsProvider):
    def query_metrics(
        self, query: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        now = datetime.now(timezone.utc)
        if "5.." in query:
            return [
                Evidence(
                    id="ev_metric_err_1",
                    type=EvidenceType.METRIC,
                    source_provider="prometheus",
                    service_resource="frontend",
                    summary="Prometheus metric: 500 error count",
                    data={
                        "metric": {
                            "__name__": "http_server_request_duration_seconds_count",
                            "http_response_status_code": "500",
                            "service_name": "frontend",
                        },
                        "values": [[now.timestamp(), "42"]],
                    },
                    observed_at=now,
                    collected_at=now,
                )
            ]
        return [
            Evidence(
                id="ev_metric_total_1",
                type=EvidenceType.METRIC,
                source_provider="prometheus",
                service_resource="frontend",
                summary="Prometheus metric: total request count",
                data={
                    "metric": {
                        "__name__": "http_server_request_duration_seconds_count",
                        "service_name": "frontend",
                    },
                    "values": [[now.timestamp(), "1000"]],
                },
                observed_at=now,
                collected_at=now,
            )
        ]


class CustomMockLogsProvider(MockLogsProvider):
    def search_logs(
        self, query: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        now = datetime.now(timezone.utc)
        return [
            Evidence(
                id="ev_log_err_1",
                type=EvidenceType.LOG,
                source_provider="opensearch",
                service_resource="frontend",
                summary="[ERROR] HTTP 500 internal server error from product-catalog",
                data={
                    "message": "HTTP 500 internal server error from product-catalog",
                    "level": "ERROR",
                },
                observed_at=now,
                collected_at=now,
            ),
            Evidence(
                id="ev_log_warn_1",
                type=EvidenceType.LOG,
                source_provider="opensearch",
                service_resource="frontend",
                summary="[WARN] slow response from product-catalog",
                data={
                    "message": "slow response from product-catalog",
                    "level": "WARN",
                },
                observed_at=now,
                collected_at=now,
            ),
        ]


class CustomMockTracesProvider(MockTracesProvider):
    def get_related_traces(
        self, service_name: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        now = datetime.now(timezone.utc)
        return [
            Evidence(
                id="ev_trace_err_1",
                type=EvidenceType.TRACE,
                source_provider="jaeger",
                service_resource="frontend",
                summary="Trace: GET /api/products | Root: frontend | Duration: 450.00ms (500 error)",
                data={
                    "trace_id": "err1",
                    "spans": [{"operation": "error", "service": "product-catalog"}],
                    "total_spans": 5,
                },
                observed_at=now,
                collected_at=now,
            ),
            Evidence(
                id="ev_trace_ok_1",
                type=EvidenceType.TRACE,
                source_provider="jaeger",
                service_resource="frontend",
                summary="Trace: GET / | Root: frontend | Duration: 20.00ms",
                data={
                    "trace_id": "ok1",
                    "spans": [{"operation": "render", "service": "frontend"}],
                    "total_spans": 2,
                },
                observed_at=now,
                collected_at=now,
            ),
        ]


@pytest.fixture
def dispatcher() -> ToolDispatcher:
    now = datetime.now(timezone.utc)
    return ToolDispatcher(
        metrics_provider=CustomMockMetricsProvider(),
        logs_provider=CustomMockLogsProvider(),
        topology_provider=MockTopologyProvider(),
        incident_start_time=now,
        traces_provider=CustomMockTracesProvider(),
    )


def test_tool_definitions_include_error_tools(dispatcher: ToolDispatcher) -> None:
    defs = dispatcher.get_tool_definitions()
    names = [d.name for d in defs]
    assert "query_error_metrics" in names
    assert "search_error_logs" in names
    assert "search_error_traces" in names
    # Generic tools still present
    assert "query_metrics" in names
    assert "search_logs" in names
    assert "search_traces" in names


def test_query_error_metrics(dispatcher: ToolDispatcher) -> None:
    call = ToolCall(id="c1", name="query_error_metrics", arguments={"service_name": "frontend"})
    result = dispatcher.dispatch(call)
    assert isinstance(result, ToolResult)
    assert len(result.evidence) >= 1
    assert result.summary is not None
    assert result.summary["tool"] == "query_error_metrics"
    assert result.summary["service"] == "frontend"
    assert result.summary["error_series_count"] == 1


def test_search_error_logs(dispatcher: ToolDispatcher) -> None:
    call = ToolCall(id="c2", name="search_error_logs", arguments={"service_name": "frontend"})
    result = dispatcher.dispatch(call)
    assert isinstance(result, ToolResult)
    assert len(result.evidence) == 2
    assert result.summary["tool"] == "search_error_logs"
    assert result.summary["total_error_logs"] == 2
    assert result.summary["severity_breakdown"]["ERROR"] == 1
    assert result.summary["severity_breakdown"]["WARN"] == 1
    assert len(result.summary["sample_error_messages"]) >= 1


def test_search_error_traces(dispatcher: ToolDispatcher) -> None:
    call = ToolCall(id="c3", name="search_error_traces", arguments={"service_name": "frontend"})
    result = dispatcher.dispatch(call)
    assert isinstance(result, ToolResult)
    assert len(result.evidence) == 2
    assert result.summary["tool"] == "search_error_traces"
    assert result.summary["total_traces"] == 2
    assert result.summary["error_traces"] == 1
    assert result.summary["healthy_traces"] == 1


def test_search_error_traces_no_provider() -> None:
    now = datetime.now(timezone.utc)
    no_trace_disp = ToolDispatcher(
        metrics_provider=MockMetricsProvider(),
        logs_provider=MockLogsProvider(),
        topology_provider=MockTopologyProvider(),
        incident_start_time=now,
        traces_provider=None, # type: ignore
    )
    call = ToolCall(id="c4", name="search_error_traces", arguments={"service_name": "frontend"})
    result = no_trace_disp.dispatch(call)
    # When traces_provider is None, registry does not have search_error_traces
    assert isinstance(result, str) and "not found" in result.lower()
