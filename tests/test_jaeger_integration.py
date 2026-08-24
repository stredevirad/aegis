import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
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
            ep = config.get("telemetry", {}).get("traces", {}).get("endpoint")
            return str(ep) if ep is not None else None
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
        
    # Generate some traffic just in case the load generator is slow
    try:
        httpx.get("http://localhost:8080", timeout=2.0)
        time.sleep(2)  # Give Jaeger time to index
    except Exception:
        pass
        
    # Query related traces for frontend in the last 15 minutes
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=15)
    
    traces = provider.get_related_traces("frontend", start_time, end_time)
    
    assert len(traces) > 0, "Expected at least one trace from frontend traffic"
    ev = traces[0]
    assert ev.type == EvidenceType.TRACE
    assert ev.source_provider == "jaeger"
    assert ev.service_resource != ""
    assert "trace_id" in ev.data
    assert "spans" in ev.data
    assert len(ev.data["spans"]) > 0, "Expected at least one span in the trace"
