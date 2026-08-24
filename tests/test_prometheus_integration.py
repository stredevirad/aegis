from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from src.adapters.prometheus import PrometheusProvider
from src.domain import EvidenceType, HealthStatus


def get_prometheus_endpoint() -> str | None:
    """Read the OTel Demo config to find the Prometheus endpoint."""
    config_path = Path(__file__).parent.parent / "config" / "opentelemetry-demo.yaml"
    if not config_path.exists():
        return None
        
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            ep = config.get("telemetry", {}).get("metrics", {}).get("endpoint")
            return str(ep) if ep is not None else None
    except Exception:
        return None

@pytest.mark.integration
def test_prometheus_live_integration() -> None:
    endpoint = get_prometheus_endpoint()
    if not endpoint:
        pytest.skip("No Prometheus endpoint configured in config/opentelemetry-demo.yaml")
        
    provider = PrometheusProvider(endpoint=endpoint)
    
    # 1. Test Health
    status = provider.get_status()
    if status.health != HealthStatus.HEALTHY:
        pytest.skip(f"Prometheus is not healthy at {endpoint}: {status.limitations}")
        
    # 2. Test Real Application Metric
    # Use a small window to ensure we get data but don't overwhelm
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=15)
    
    query = "frontend"
    
    evidence_list = provider.query_metrics(query, start_time, end_time)
    
    # Assert we got at least one evidence object back
    assert len(evidence_list) > 0, "No metrics returned for application query in the last 15 minutes."
    
    # Check the structure of the first evidence object
    ev = evidence_list[0]
    assert ev.type == EvidenceType.METRIC
    assert ev.source_provider == "prometheus"
    assert query in ev.data["query"]
    assert "values" in ev.data
    assert len(ev.data["values"]) > 0
    assert ev.observed_at is not None
    assert ev.collected_at is not None
