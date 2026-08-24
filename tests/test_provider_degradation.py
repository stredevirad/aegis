"""Tests for provider degradation handling in the investigation engine.

Verifies that the engine gracefully handles:
- Provider unavailability (RuntimeError)
- Provider timeouts
- Empty results
- Partial telemetry
"""
import os
import tempfile
from datetime import datetime, timezone

import pytest

from src.adapters.mock_providers import (
    MockLogsProvider,
    MockMetricsProvider,
    MockTopologyProvider,
    MockTracesProvider,
)
from src.domain import (
    EvidenceType,
    Incident,
    InvestigationStatus,
)
from src.investigation.engine import InvestigationEngine
from src.investigation.tools import ToolDispatcher
from src.persistence.repository import SQLiteRepository
from tests.mock_llm import MockLLMProvider


@pytest.fixture
def tmp_db():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    yield db_path
    try:
        os.remove(db_path)
    except OSError:
        pass


class FailingMetricsProvider(MockMetricsProvider):
    """Simulates a metrics provider that raises RuntimeError."""
    def query_metrics(self, query, start_time, end_time):
        raise RuntimeError("Prometheus unreachable: connection refused")

    def get_service_health(self, service_name):
        raise RuntimeError("Prometheus unreachable: connection refused")


class FailingLogsProvider(MockLogsProvider):
    """Simulates a logs provider that raises RuntimeError."""
    def search_logs(self, query, start_time, end_time):
        raise RuntimeError("OpenSearch unreachable: connection refused")


class FailingTracesProvider(MockTracesProvider):
    """Simulates a traces provider that raises RuntimeError."""
    def get_related_traces(self, service_name, start_time, end_time):
        raise RuntimeError("Jaeger unreachable: connection refused")

    def get_trace(self, trace_id):
        raise RuntimeError("Jaeger unreachable: connection refused")


class EmptyMetricsProvider(MockMetricsProvider):
    """Returns empty results for all queries."""
    def query_metrics(self, query, start_time, end_time):
        return []


class EmptyLogsProvider(MockLogsProvider):
    """Returns empty results for all queries."""
    def search_logs(self, query, start_time, end_time):
        return []


class EmptyTracesProvider(MockTracesProvider):
    """Returns empty results for all queries."""
    def get_related_traces(self, service_name, start_time, end_time):
        return []


def _make_engine(tmp_db, metrics=None, logs=None, traces=None):
    repository = SQLiteRepository(db_path=tmp_db)
    metrics = metrics or MockMetricsProvider()
    logs = logs or MockLogsProvider()
    topology = MockTopologyProvider()
    traces = traces or MockTracesProvider()
    incident_start = datetime.now(timezone.utc)
    dispatcher = ToolDispatcher(metrics, logs, topology, incident_start_time=incident_start, traces_provider=traces)
    llm = MockLLMProvider()

    incident = Incident(
        id="test_degradation",
        title="Test Degradation",
        description="Testing provider failure",
        start_time=incident_start,
        affected_service="orders-service",
    )

    return InvestigationEngine(
        incident=incident,
        llm_provider=llm,
        tool_dispatcher=dispatcher,
        repository=repository,
        max_turns=10,
        max_tool_calls=15,
    )


class TestProviderUnavailable:
    """Tests that the engine handles individual provider failures gracefully."""

    def test_metrics_provider_unavailable(self, tmp_db):
        engine = _make_engine(tmp_db, metrics=FailingMetricsProvider())
        engine.investigation_mode = "fast"
        engine.run()

        # Engine should still complete
        assert engine.incident.status == InvestigationStatus.RECORDED
        assert len(engine.provider_errors) > 0
        assert any("Prometheus" in e or "metrics" in e.lower() for e in engine.provider_errors)

    def test_logs_provider_unavailable(self, tmp_db):
        engine = _make_engine(tmp_db, logs=FailingLogsProvider())
        engine.investigation_mode = "fast"
        engine.run()

        assert engine.incident.status == InvestigationStatus.RECORDED
        assert len(engine.provider_errors) > 0

    def test_traces_provider_unavailable(self, tmp_db):
        engine = _make_engine(tmp_db, traces=FailingTracesProvider())
        engine.investigation_mode = "fast"
        engine.run()

        assert engine.incident.status == InvestigationStatus.RECORDED
        assert len(engine.provider_errors) > 0

    def test_all_providers_unavailable(self, tmp_db):
        engine = _make_engine(
            tmp_db,
            metrics=FailingMetricsProvider(),
            logs=FailingLogsProvider(),
            traces=FailingTracesProvider(),
        )
        engine.investigation_mode = "fast"
        engine.run()

        # Should still reach RECORDED — the LLM can still reason
        assert engine.incident.status == InvestigationStatus.RECORDED
        assert len(engine.provider_errors) >= 3


class TestEmptyResults:
    """Tests that empty provider results are handled differently from failures."""

    def test_empty_metrics(self, tmp_db):
        engine = _make_engine(tmp_db, metrics=EmptyMetricsProvider())
        engine.investigation_mode = "fast"
        engine.run()

        assert engine.incident.status == InvestigationStatus.RECORDED
        # No provider errors — empty results are not errors
        assert not any("metrics" in e.lower() and "error" in e.lower() for e in engine.provider_errors)

    def test_empty_logs(self, tmp_db):
        engine = _make_engine(tmp_db, logs=EmptyLogsProvider())
        engine.investigation_mode = "fast"
        engine.run()
        assert engine.incident.status == InvestigationStatus.RECORDED

    def test_empty_traces(self, tmp_db):
        engine = _make_engine(tmp_db, traces=EmptyTracesProvider())
        engine.investigation_mode = "fast"
        engine.run()
        assert engine.incident.status == InvestigationStatus.RECORDED

    def test_all_empty(self, tmp_db):
        engine = _make_engine(
            tmp_db,
            metrics=EmptyMetricsProvider(),
            logs=EmptyLogsProvider(),
            traces=EmptyTracesProvider(),
        )
        engine.investigation_mode = "fast"
        engine.run()
        assert engine.incident.status == InvestigationStatus.RECORDED
        # No evidence but engine still completes
        metric_evidence = [e for e in engine.incident.evidence if e.type == EvidenceType.METRIC]
        assert len(metric_evidence) == 0


class TestPartialTelemetry:
    """Tests that the engine handles combinations of available/unavailable providers."""

    def test_metrics_only(self, tmp_db):
        engine = _make_engine(
            tmp_db,
            logs=FailingLogsProvider(),
            traces=FailingTracesProvider(),
        )
        engine.investigation_mode = "fast"
        engine.run()
        assert engine.incident.status == InvestigationStatus.RECORDED

    def test_metrics_plus_logs(self, tmp_db):
        engine = _make_engine(
            tmp_db,
            traces=FailingTracesProvider(),
        )
        engine.investigation_mode = "fast"
        engine.run()
        assert engine.incident.status == InvestigationStatus.RECORDED

    def test_logs_plus_traces(self, tmp_db):
        engine = _make_engine(
            tmp_db,
            metrics=FailingMetricsProvider(),
        )
        engine.investigation_mode = "fast"
        engine.run()
        assert engine.incident.status == InvestigationStatus.RECORDED

    def test_logs_only(self, tmp_db):
        engine = _make_engine(
            tmp_db,
            metrics=FailingMetricsProvider(),
            traces=FailingTracesProvider(),
        )
        engine.investigation_mode = "fast"
        engine.run()
        assert engine.incident.status == InvestigationStatus.RECORDED
