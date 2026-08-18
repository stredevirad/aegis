import pytest

from src.adapters.ollama import OllamaProvider
from src.domain import HealthStatus, LLMRequest


@pytest.mark.integration
def test_ollama_integration():
    provider = OllamaProvider()

    status = provider.get_status()
    if status.health != HealthStatus.HEALTHY:
        pytest.skip(f"Ollama not available or healthy: {status.limitations}")

    request = LLMRequest(prompt="Say 'Hello, World!' and nothing else.")
    response = provider.generate(request)

    assert response.error is None
    assert "Hello" in response.content
    assert response.model_name == "qwen3.5:9b"
    assert response.latency_ms is not None
    assert response.latency_ms > 0
