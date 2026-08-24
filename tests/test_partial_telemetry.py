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
    Evidence,
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

class FailingProviderMixIn:
    def fail(self):
        raise RuntimeError("Provider simulated failure")

class FailingMetrics(MockMetricsProvider, FailingProviderMixIn):
    def query_metrics(self, query, start_time, end_time): self.fail()

class FailingLogs(MockLogsProvider, FailingProviderMixIn):
    def search_logs(self, query, start_time, end_time): self.fail()

class FailingTraces(MockTracesProvider, FailingProviderMixIn):
    def get_related_traces(self, service_name, start_time, end_time): self.fail()

class WorkingTraces(MockTracesProvider):
    def get_related_traces(self, service_name, start_time, end_time):
        from src.domain import EvidenceType
        return [
            Evidence(
                id="ev_trace_test",
                type=EvidenceType.TRACE,
                source_provider="mock-traces",
                service_resource=service_name,
                summary="Trace for " + service_name,
                observed_at=start_time,
                collected_at=start_time,
                data={"trace_id": "test"}
            )
        ]

def _make_engine(tmp_db, metrics, logs, traces):
    repository = SQLiteRepository(db_path=tmp_db)
    topology = MockTopologyProvider()
    incident_start = datetime.now(timezone.utc)
    dispatcher = ToolDispatcher(metrics, logs, topology, incident_start_time=incident_start, traces_provider=traces)
    llm = MockLLMProvider()
    incident = Incident(
        id="test_partial", title="Partial Test", description="Testing", start_time=incident_start, affected_service="orders-service"
    )
    return InvestigationEngine(
        incident=incident, llm_provider=llm, tool_dispatcher=dispatcher, repository=repository, max_turns=10, max_tool_calls=15
    )

def test_metrics_only(tmp_db):
    engine = _make_engine(tmp_db, metrics=MockMetricsProvider(), logs=FailingLogs(), traces=FailingTraces())
    engine.investigation_mode = "fast"
    engine.run()
    assert engine.incident.status == InvestigationStatus.RECORDED
    assert any(e.type == EvidenceType.METRIC for e in engine.incident.evidence)
    assert not any(e.type == EvidenceType.LOG for e in engine.incident.evidence)

def test_metrics_plus_logs(tmp_db):
    engine = _make_engine(tmp_db, metrics=MockMetricsProvider(), logs=MockLogsProvider(), traces=FailingTraces())
    engine.investigation_mode = "fast"
    engine.run()
    assert engine.incident.status == InvestigationStatus.RECORDED
    types = {e.type for e in engine.incident.evidence}
    assert EvidenceType.METRIC in types
    assert EvidenceType.LOG in types

def test_metrics_plus_traces(tmp_db):
    engine = _make_engine(tmp_db, metrics=MockMetricsProvider(), logs=FailingLogs(), traces=WorkingTraces())
    engine.investigation_mode = "fast"
    engine.run()
    assert engine.incident.status == InvestigationStatus.RECORDED
    types = {e.type for e in engine.incident.evidence}
    assert EvidenceType.METRIC in types
    assert EvidenceType.TRACE in types

def test_logs_plus_traces(tmp_db):
    engine = _make_engine(tmp_db, metrics=FailingMetrics(), logs=MockLogsProvider(), traces=WorkingTraces())
    engine.investigation_mode = "fast"
    engine.run()
    assert engine.incident.status == InvestigationStatus.RECORDED
    types = {e.type for e in engine.incident.evidence}
    assert EvidenceType.LOG in types
    assert EvidenceType.TRACE in types

def test_logs_only(tmp_db):
    engine = _make_engine(tmp_db, metrics=FailingMetrics(), logs=MockLogsProvider(), traces=FailingTraces())
    engine.investigation_mode = "fast"
    engine.run()
    assert engine.incident.status == InvestigationStatus.RECORDED
    types = {e.type for e in engine.incident.evidence}
    assert EvidenceType.LOG in types
    assert EvidenceType.METRIC not in types
