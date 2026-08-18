import pytest

from src.adapters.ollama import OllamaProvider
from src.domain import HealthStatus, LLMRequest, FinishReason


@pytest.mark.integration
def test_ollama_connection() -> None:
    # Requires Ollama running locally with qwen3.5:9b
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen3.5:9b")
    
    # Just test basic connectivity and completion
    request = LLMRequest(prompt="Say hello.")
    resp = provider.generate(request)
    
    # Don't assert the exact content, just that it didn't fail
    assert resp.finish_reason != FinishReason.ERROR
    assert resp.content is not None
    assert len(resp.content) > 0
    assert resp.model_name == "qwen3.5:9b"
    assert resp.latency_ms is not None
    assert resp.latency_ms > 0
