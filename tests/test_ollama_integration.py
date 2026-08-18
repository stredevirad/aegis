import pytest

from src.adapters.ollama import OllamaProvider
from src.domain import FinishReason, HealthStatus, LLMRequest


@pytest.mark.integration
def test_ollama_connection() -> None:
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen3.5:9b")
    
    status = provider.get_status()
    if status.health != HealthStatus.HEALTHY:
        pytest.skip(f"Ollama not available or healthy: {status.limitations}")
        
    request = LLMRequest(prompt="Say hello.")
    resp = provider.generate(request)
    
    assert resp.finish_reason != FinishReason.ERROR
    assert resp.content is not None
    assert len(resp.content) > 0
    assert resp.model_name == "qwen3.5:9b"
    assert resp.latency_ms is not None
    assert resp.latency_ms > 0

def test_ollama_payload_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import Any

    from src.domain import LLMMessage, MessageRole, ToolCall
    provider = OllamaProvider(base_url="http://localhost:11434", model="test-model")
    provider.default_think = True
    
    captured_payload: dict[str, Any] = {}
    
    class MockResponse:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict[str, Any]: return {"message": {"content": "mock"}}
    
    class MockClient:
        def __init__(self, **kwargs: Any) -> None: pass
        def __enter__(self) -> 'MockClient': return self
        def __exit__(self, *args: object) -> None: pass
        def post(self, url: str, json: dict[str, Any], **kwargs: Any) -> MockResponse:
            nonlocal captured_payload
            captured_payload = json
            return MockResponse()

    monkeypatch.setattr("httpx.Client", MockClient)
    
    request = LLMRequest(
        messages=[
            LLMMessage(role=MessageRole.USER, content="Hello"),
            LLMMessage(
                role=MessageRole.ASSISTANT, 
                content="", 
                tool_calls=[ToolCall(id="tc1", name="query_metrics", arguments={"q": "up"})]
            ),
            LLMMessage(role=MessageRole.TOOL, content="result", tool_call_id="tc1", tool_name="query_metrics")
        ]
    )
    provider.generate(request)
    
    # 1. Root-level think parameter
    assert "think" in captured_payload
    assert captured_payload["think"] is True
    
    # 2. Assistant tool-call serialization
    assistant_msg = captured_payload["messages"][1]
    assert assistant_msg["role"] == "assistant"
    assert "tool_calls" in assistant_msg
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "query_metrics"
    
    # 3. Tool result serialization contains tool_name
    tool_msg = captured_payload["messages"][2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["content"] == "result"
    assert tool_msg["tool_name"] == "query_metrics"
