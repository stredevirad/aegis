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


def test_engine_state_transitions(engine):
    engine.run()
    if engine.incident.status == InvestigationStatus.FAILED:
        print(f"\nFAILED REASON: {engine.messages[-1].content}")
    assert engine.incident.status == InvestigationStatus.RECORDED
    assert engine.turn_count == 6
    assert engine.tool_call_count == 2
    assert engine.incident.end_time is not None
    assert len(engine.incident.hypotheses) == 2
    assert engine.incident.hypotheses[0].description == "Downstream DB latency caused payment-api to fail."


def test_engine_budget_enforcement(engine):
    # Set budgets too low so it fails
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
