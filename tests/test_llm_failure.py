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
    FinishReason,
    HealthStatus,
    Incident,
    InvestigationStatus,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderStatus,
)
from src.investigation.engine import InvestigationEngine
from src.investigation.tools import ToolDispatcher
from src.persistence.repository import SQLiteRepository


@pytest.fixture
def tmp_db():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    yield db_path
    try:
        os.remove(db_path)
    except OSError:
        pass

class ErrorLLM(LLMProvider):
    def get_status(self) -> ProviderStatus:
        return ProviderStatus(provider="error-llm", health=HealthStatus.HEALTHY)
    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(content="", finish_reason=FinishReason.ERROR, model_name="error-llm", error="Connection timeout")

class MalformedLLM(LLMProvider):
    def get_status(self) -> ProviderStatus:
        return ProviderStatus(provider="malformed-llm", health=HealthStatus.HEALTHY)
    def generate(self, request: LLMRequest) -> LLMResponse:
        # Returns empty or malformed content without expected tags
        return LLMResponse(content="I think it's broken.", finish_reason=FinishReason.STOP, model_name="malformed-llm")

def _make_engine(tmp_db, llm):
    repository = SQLiteRepository(db_path=tmp_db)
    metrics = MockMetricsProvider()
    logs = MockLogsProvider()
    topology = MockTopologyProvider()
    traces = MockTracesProvider()
    incident_start = datetime.now(timezone.utc)
    dispatcher = ToolDispatcher(metrics, logs, topology, incident_start_time=incident_start, traces_provider=traces)
    incident = Incident(
        id="test_llm_fail", title="LLM Test", description="Testing", start_time=incident_start, affected_service="payment-api"
    )
    return InvestigationEngine(
        incident=incident, llm_provider=llm, tool_dispatcher=dispatcher, repository=repository, max_turns=3, max_tool_calls=5
    )

def test_llm_error_reason(tmp_db):
    engine = _make_engine(tmp_db, ErrorLLM())
    engine.investigation_mode = "fast"
    engine.run()
    # Should transition to FAILED
    assert engine.incident.status == InvestigationStatus.FAILED
    assert "Error: LLM Error: Connection timeout" in [m.content for m in engine.messages if getattr(m, "content", "")]

def test_llm_malformed_response(tmp_db):
    engine = _make_engine(tmp_db, MalformedLLM())
    engine.investigation_mode = "fast"
    engine.run()
    # The engine should retry max_turns times (asking for HYPOTHESIS), then fail due to budget
    assert engine.incident.status == InvestigationStatus.FAILED
    assert engine.turn_count >= 3
