from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.domain.llm_models import (
    FinishReason,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    MessageRole,
    ToolCall,
    ToolDefinition,
)
from src.domain.models import (
    Evidence,
    EvidenceType,
    HealthStatus,
    Hypothesis,
    HypothesisStatus,
    Incident,
    InvestigationStatus,
    ProviderStatus,
    ServiceHealth,
)

# --- Evidence ---


def test_evidence_creation() -> None:
    now = datetime.now(timezone.utc)
    evidence = Evidence(
        id="e1",
        type=EvidenceType.METRIC,
        source_provider="prometheus",
        summary="High CPU usage",
        observed_at=now,
        collected_at=now,
    )
    assert evidence.type == EvidenceType.METRIC
    assert evidence.data == {}
    assert evidence.observed_at.tzinfo is not None
    assert evidence.service_resource is None


def test_naive_datetime_rejected_on_evidence() -> None:
    naive_now = datetime.now()  # noqa: DTZ005
    with pytest.raises(ValidationError):
        Evidence(
            id="e1",
            type=EvidenceType.METRIC,
            source_provider="prometheus",
            summary="test",
            observed_at=naive_now,
            collected_at=naive_now,
        )


# --- Hypothesis ---


def test_hypothesis_defaults() -> None:
    h = Hypothesis(id="h1", description="DB failure")
    assert h.confidence is None
    assert h.status == HypothesisStatus.UNKNOWN
    assert h.supporting_evidence_ids == []
    assert h.contradicting_evidence_ids == []


def test_hypothesis_confidence_valid() -> None:
    h = Hypothesis(id="h1", description="Network partition", confidence=0.85)
    assert h.confidence == 0.85


def test_hypothesis_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        Hypothesis(id="h1", description="Too high", confidence=1.5)
    with pytest.raises(ValidationError):
        Hypothesis(id="h1", description="Too low", confidence=-0.1)


# --- Incident ---


def test_incident_defaults() -> None:
    now = datetime.now(timezone.utc)
    incident = Incident(
        id="i1",
        title="Outage",
        description="Service down",
        start_time=now,
    )
    assert incident.status == InvestigationStatus.INCIDENT_CREATED
    assert incident.end_time is None
    assert incident.hypotheses == []
    assert incident.evidence == []
    assert incident.available_capabilities == []
    assert incident.scenario_id is None


def test_incident_status_is_typed_enum() -> None:
    now = datetime.now(timezone.utc)
    incident = Incident(
        id="i1",
        title="Outage",
        description="test",
        start_time=now,
        status=InvestigationStatus.COLLECTING_EVIDENCE,
    )
    assert incident.status == InvestigationStatus.COLLECTING_EVIDENCE


def test_incident_failed_state() -> None:
    now = datetime.now(timezone.utc)
    incident = Incident(
        id="i1",
        title="Broken",
        description="test",
        start_time=now,
        status=InvestigationStatus.FAILED,
    )
    assert incident.status == InvestigationStatus.FAILED


def test_incident_naive_datetime_rejected() -> None:
    naive_now = datetime.now()  # noqa: DTZ005
    with pytest.raises(ValidationError):
        Incident(id="i1", title="x", description="x", start_time=naive_now)


def test_incident_end_time() -> None:
    now = datetime.now(timezone.utc)
    incident = Incident(
        id="i1",
        title="Done",
        description="test",
        start_time=now,
        end_time=now,
        status=InvestigationStatus.RECORDED,
    )
    assert incident.end_time is not None


# --- ServiceHealth ---


def test_service_health_defaults() -> None:
    health = ServiceHealth(service_id="api-gateway")
    assert health.status == HealthStatus.UNKNOWN
    assert health.metadata == {}


def test_service_health_explicit() -> None:
    health = ServiceHealth(service_id="api-gateway", status=HealthStatus.HEALTHY)
    assert health.status == HealthStatus.HEALTHY


# --- ProviderStatus ---


def test_provider_status_uses_health_status() -> None:
    ps = ProviderStatus(provider="prometheus", health=HealthStatus.HEALTHY)
    assert ps.health == HealthStatus.HEALTHY
    assert ps.capabilities == []


# --- LLM Models ---


def test_llm_request_single_prompt() -> None:
    req = LLMRequest(prompt="Analyze this")
    assert req.messages == []
    assert req.tools == []


def test_llm_request_multi_turn() -> None:
    req = LLMRequest(
        messages=[
            LLMMessage(role=MessageRole.SYSTEM, content="You are an investigator."),
            LLMMessage(role=MessageRole.USER, content="What caused the outage?"),
        ],
        thinking_mode=True,
    )
    assert len(req.messages) == 2
    assert req.messages[0].role == MessageRole.SYSTEM


def test_llm_response_token_count():
    response = LLMResponse(content="test", finish_reason=FinishReason.STOP, token_count=42, model_name="mock")
    assert response.token_count == 42


def test_llm_response_no_token_count():
    response = LLMResponse(content="test", finish_reason=FinishReason.STOP, model_name="mock")
    assert response.token_count is None


def test_llm_response_with_error() -> None:
    resp = LLMResponse(
        content="",
        finish_reason=FinishReason.ERROR,
        error="Model not loaded",
        model_name="mock",
    )
    assert resp.finish_reason == FinishReason.ERROR
    assert resp.error == "Model not loaded"


def test_llm_response_with_tool_calls() -> None:
    resp = LLMResponse(
        content="",
        tool_calls=[
            ToolCall(id="tc1", name="query_metrics", arguments={"query": "up"}),
        ],
        finish_reason=FinishReason.TOOL_CALL,
        model_name="mock",
    )
    assert resp.finish_reason == FinishReason.TOOL_CALL
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "query_metrics"


def test_llm_response_research_metadata() -> None:
    resp = LLMResponse(
        content="Done",
        model_name="qwen3.5:9b",
        token_count=150,
        latency_ms=1200.5,
    )
    assert resp.token_count == 150
    assert resp.latency_ms == 1200.5


def test_tool_definition() -> None:
    td = ToolDefinition(
        name="get_service_health",
        description="Get the health status of a service",
        parameters={"service_name": {"type": "string"}},
    )
    assert td.name == "get_service_health"
    assert td.parameters != {}


def test_tool_call() -> None:
    tc = ToolCall(id="tc1", name="search_logs", arguments={"query": "error"})
    assert tc.name == "search_logs"
    assert tc.arguments == {"query": "error"}
