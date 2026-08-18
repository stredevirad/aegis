
from src.domain import (
    FinishReason,
    HealthStatus,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderStatus,
    ToolCall,
)


class MockLLMProvider(LLMProvider):
    """A deterministic mock LLM provider that properly exercises the full state machine."""
    
    def __init__(self) -> None:
        self.turn = 0
        self.iterative_turn = 0

    def get_status(self) -> ProviderStatus:
        return ProviderStatus(provider="mock-llm", health=HealthStatus.HEALTHY)

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.turn += 1
        
        last_msg = request.messages[-1].content if request.messages else ""
        
        if "INITIAL EVIDENCE BUNDLE:" in last_msg:
            return LLMResponse(
                content="HYPOTHESIS: Downstream DB latency caused payment-api to fail.\nHYPOTHESIS: Payment API is simply overloaded.",
                tool_calls=[ToolCall(id="mock_verif", name="search_logs", arguments={"query": "orders-service errors"})],
                finish_reason=FinishReason.TOOL_CALL,
                model_name="mock-llm",
            )
        elif "DETERMINISTIC VERIFICATION EVIDENCE:" in last_msg:
            return LLMResponse(
                content="DIAGNOSIS: High latency in downstream-db caused payment-api to fail.\nRECOMMENDATION: Scale up downstream-db replicas | REASON: The root cause is high latency due to load | IMPACT: Resolves timeout errors in payment-api | RISK: Low risk, standard horizontal scaling operation.",
                finish_reason=FinishReason.STOP,
                model_name="mock-llm",
            )
            
        self.iterative_turn += 1
        
        if self.iterative_turn == 1:
            # First turn: COLLECTING_EVIDENCE
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id="tc1", name="query_metrics", arguments={"query": "payment-api latency"})],
                finish_reason=FinishReason.TOOL_CALL,
                model_name="mock-llm",
            )
        elif self.iterative_turn == 2:
            # Second turn: Still COLLECTING_EVIDENCE, but we decide we have enough and output nothing.
            return LLMResponse(
                content="I have collected initial evidence.",
                tool_calls=[],
                finish_reason=FinishReason.STOP,
                model_name="mock-llm",
            )
        elif self.iterative_turn == 3:
            # Third turn: FORMING_HYPOTHESES
            return LLMResponse(
                content="HYPOTHESIS: Downstream DB latency caused payment-api to fail.\nHYPOTHESIS: Payment API is simply overloaded.",
                tool_calls=[],
                finish_reason=FinishReason.STOP,
                model_name="mock-llm",
            )
        elif self.iterative_turn == 4:
            # Fourth turn: VERIFYING_HYPOTHESES
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id="tc2", name="search_logs", arguments={"query": "payment-api errors"})],
                finish_reason=FinishReason.TOOL_CALL,
                model_name="mock-llm",
            )
        elif self.iterative_turn == 5:
            # Fifth turn: Still VERIFYING_HYPOTHESES, we conclude.
            return LLMResponse(
                content="DIAGNOSIS: High latency in downstream-db caused payment-api to fail.",
                tool_calls=[],
                finish_reason=FinishReason.STOP,
                model_name="mock-llm",
            )
        elif self.iterative_turn == 6:
            # Sixth turn: RECOMMENDATION
            return LLMResponse(
                content="RECOMMENDATION: Scale up downstream-db replicas | REASON: The root cause is high latency due to load | IMPACT: Resolves timeout errors in payment-api | RISK: Low risk, standard horizontal scaling operation.",
                tool_calls=[],
                finish_reason=FinishReason.STOP,
                model_name="mock-llm",
            )
        else:
            return LLMResponse(
                content="I'm done.",
                finish_reason=FinishReason.STOP,
                model_name="mock-llm",
            )
