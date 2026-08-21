import os
import tempfile
from datetime import datetime, timezone

import pytest

from src.adapters.mock_providers import MockLogsProvider, MockMetricsProvider, MockTopologyProvider
from src.domain import Incident, InvestigationStatus, ToolCall
from src.investigation.engine import InvestigationEngine
from src.investigation.tools import ToolDispatcher
from src.persistence.repository import SQLiteRepository
from tests.mock_llm import MockLLMProvider


@pytest.fixture
def engine():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    
    repository = SQLiteRepository(db_path=db_path)
    metrics = MockMetricsProvider()
    logs = MockLogsProvider()
    topology = MockTopologyProvider()
    incident_start = datetime.now(timezone.utc)
    dispatcher = ToolDispatcher(metrics, logs, topology, incident_start_time=incident_start)
    llm = MockLLMProvider()
    
    incident = Incident(
        id="test_incident",
        title="Test Incident",
        description="A test",
        start_time=incident_start,
    )
    
    engine = InvestigationEngine(
        incident=incident,
        llm_provider=llm,
        tool_dispatcher=dispatcher,
        repository=repository,
        max_turns=10,
        max_tool_calls=5,
    )
    
    yield engine
    
    try:
        os.remove(db_path)
    except OSError:
        pass


def test_engine_state_transitions_fast(engine: InvestigationEngine) -> None:
    engine.incident.affected_service = "orders-service"
    engine.investigation_mode = "fast"
    engine.run()
    if engine.incident.status == InvestigationStatus.FAILED:
        print(f"\nFAILED REASON: {engine.messages[-1].content}")
    assert engine.incident.status == InvestigationStatus.RECORDED
    assert engine.turn_count == 2
    # 2 for initial collection, + however many MockLLM generates (MockLLM doesn't generate tools in fast mode currently, wait we need to update MockLLM for that!)
    assert engine.incident.end_time is not None
    assert len(engine.incident.hypotheses) == 2
    assert engine.incident.hypotheses[0].description == "Downstream DB latency caused payment-api to fail."
    
    # Evidence checks
    assert len(engine.incident.evidence) >= 2 # 2 initial + verification
    
    # SQLite check
    loaded = engine.repository.load_incident(engine.incident.id)
    assert loaded is not None
    assert len(loaded.evidence) == len(engine.incident.evidence)

def test_engine_state_transitions_iterative(engine: InvestigationEngine) -> None:
    engine.investigation_mode = "iterative"
    engine.run()
    if engine.incident.status == InvestigationStatus.FAILED:
        print(f"\nFAILED REASON: {engine.messages[-1].content}")
    assert engine.incident.status == InvestigationStatus.RECORDED
    assert engine.turn_count == 6
    assert engine.tool_call_count == 2
    assert engine.incident.end_time is not None
    assert len(engine.incident.hypotheses) == 2
    assert engine.incident.hypotheses[0].description == "Downstream DB latency caused payment-api to fail."

def test_engine_run_budgets(engine: InvestigationEngine) -> None:
    # Set budgets too low so it fails
    engine.investigation_mode = "iterative"
    engine.max_turns = 2
    engine.run()
    
    assert engine.incident.status == InvestigationStatus.FAILED


def test_tool_validation(engine):
    # Pass missing query argument to query_metrics
    tc = ToolCall(id="tc_bad", name="query_metrics", arguments={})
    result = engine.tool_dispatcher.dispatch(tc)
    assert "Invalid arguments" in result
    assert "query" in result

    # Pass entirely wrong argument type
    tc2 = ToolCall(id="tc_bad2", name="get_service_health", arguments={"service_name": {"obj": "invalid"}})
    result2 = engine.tool_dispatcher.dispatch(tc2)
    assert "Invalid arguments" in result2
    assert "Input should be a valid string" in result2

def test_engine_duplicate_tool_calls(engine: InvestigationEngine) -> None:
    engine.investigation_mode = "iterative"
    from src.domain import (
        FinishReason,
        HealthStatus,
        LLMProvider,
        LLMRequest,
        LLMResponse,
        ProviderStatus,
        ToolCall,
    )
    
    # Fake LLM that only generates the same tool call
    class LoopingMockLLM(LLMProvider):
        def __init__(self) -> None:
            self.model = "mock"
        def get_status(self) -> ProviderStatus:
            return ProviderStatus(provider="mock", health=HealthStatus.HEALTHY)
        def generate(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id="tc1", name="query_metrics", arguments={"query": "up"})],
                finish_reason=FinishReason.TOOL_CALL,
                model_name="mock"
            )
            
    engine.llm_provider = LoopingMockLLM()
    engine.run()
    
    # It should hit the duplicate loop threshold and eventually fail or transition
    assert engine.duplicate_call_count > 0
    # Make sure we didn't just loop infinitely
    assert engine.turn_count <= engine.max_turns

def test_engine_tools_cleared_in_forming_hypotheses(engine: InvestigationEngine) -> None:
    engine.investigation_mode = "iterative"
    from src.domain import (
        FinishReason,
        HealthStatus,
        InvestigationStatus,
        LLMProvider,
        LLMRequest,
        LLMResponse,
        ProviderStatus,
        ToolDefinition,
    )
    
    engine.incident.status = InvestigationStatus.FORMING_HYPOTHESES
    
    class InspectingMockLLM(LLMProvider):
        def __init__(self) -> None:
            self.model = "mock"
            self.received_tools: list[ToolDefinition] | None = None
        def get_status(self) -> ProviderStatus:
            return ProviderStatus(provider="mock", health=HealthStatus.HEALTHY)
        def generate(self, request: LLMRequest) -> LLMResponse:
            self.received_tools = request.tools
            return LLMResponse(content="HYPOTHESIS: test", finish_reason=FinishReason.STOP, model_name="mock")
            
    mock_llm = InspectingMockLLM()
    engine.llm_provider = mock_llm
    
    engine._step()
    
    assert mock_llm.received_tools == []


def test_engine_sqlite_evidence_equality(engine: InvestigationEngine) -> None:
    from src.domain.models import Evidence, EvidenceType
    import time
    
    ev_id = f"ev_verif_{int(time.time()*1000)}"
    ev = Evidence(
        id=ev_id,
        type=EvidenceType.METRIC,
        source_provider="mock-metrics",
        summary="Test summary",
        service_resource="orders-service",
        data={"key": "value"},
        observed_at=engine.incident.start_time,
        collected_at=datetime.now(timezone.utc)
    )
    engine.incident.evidence.append(ev)
    engine.repository.save_incident(engine.incident)
    
    loaded = engine.repository.load_incident(engine.incident.id)
    assert loaded is not None
    assert len(loaded.evidence) == 1
    
    loaded_ev = loaded.evidence[0]
    assert loaded_ev.id == ev.id
    assert loaded_ev.type == ev.type
    assert loaded_ev.source_provider == ev.source_provider
    assert loaded_ev.summary == ev.summary
    assert loaded_ev.service_resource == ev.service_resource
    assert loaded_ev.data == ev.data
    assert loaded_ev.observed_at == ev.observed_at
    assert loaded_ev.collected_at == ev.collected_at
