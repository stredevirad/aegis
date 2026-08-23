from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from src.adapters.jaeger import JaegerProvider
from src.domain import EvidenceType, HealthStatus


def get_jaeger_endpoint() -> str | None:
    config_path = Path(__file__).parent.parent / "config" / "opentelemetry-demo.yaml"
    if not config_path.exists():
        return None
        
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            return config.get("telemetry", {}).get("traces", {}).get("endpoint")
    except Exception:
        return None

@pytest.mark.integration
def test_jaeger_live_integration() -> None:
    endpoint = get_jaeger_endpoint()
    if not endpoint:
        pytest.skip("No Jaeger endpoint found in config")
        
    provider = JaegerProvider(endpoint=endpoint)
    status = provider.get_status()
    
    if status.health == HealthStatus.UNAVAILABLE:
        pytest.skip("Jaeger is unavailable")
        
    # Query related traces for frontend in the last 15 minutes
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=15)
    
    traces = provider.get_related_traces("frontend", start_time, end_time)
    
    # Depending on traffic, there might be 0 traces, but if there are traces, assert structure
    if traces:
        assert traces[0].type == EvidenceType.TRACE
        assert traces[0].source_provider == "jaeger"
        assert traces[0].service_resource is not None
        assert "trace_id" in traces[0].data
        assert "spans" in traces[0].data
