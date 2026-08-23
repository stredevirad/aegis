import pytest
from datetime import datetime, timezone, timedelta
import yaml
from pathlib import Path

from src.adapters.opensearch import OpenSearchProvider
from src.domain import EvidenceType, HealthStatus

def get_opensearch_endpoint() -> str | None:
    """Read the OTel Demo config to find the OpenSearch endpoint."""
    config_path = Path(__file__).parent.parent / "config" / "opensearch-demo.yaml"
    # Wait, the config file is opentelemetry-demo.yaml
    config_path = Path(__file__).parent.parent / "config" / "opentelemetry-demo.yaml"
    if not config_path.exists():
        return None
        
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            return config.get("telemetry", {}).get("logs", {}).get("endpoint")
    except Exception:
        return None

@pytest.mark.integration
def test_opensearch_live_integration() -> None:
    endpoint = get_opensearch_endpoint()
    if not endpoint:
        pytest.skip("No OpenSearch endpoint configured in config/opentelemetry-demo.yaml")
        
    provider = OpenSearchProvider(endpoint=endpoint)
    
    # 1. Test Health
    status = provider.get_status()
    if status.health != HealthStatus.HEALTHY:
        pytest.skip(f"OpenSearch is not healthy at {endpoint}: {status.limitations}")
        
    # 2. Test Real Application Log
    # Use a small window to ensure we get data but don't overwhelm
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=15)
    
    query = "frontend"
    
    evidence_list = provider.search_logs(query, start_time, end_time)
    
    # Assert we got at least one evidence object back
    # Usually frontend-proxy generates lots of proxy.access logs
    assert len(evidence_list) > 0, "No logs returned for application query in the last 15 minutes."
    
    # Check the structure of the first evidence object
    ev = evidence_list[0]
    assert ev.type == EvidenceType.LOG
    assert ev.source_provider == "opensearch"
    assert ev.service_resource is not None
    assert "message" in ev.data
    assert "level" in ev.data
    assert ev.observed_at is not None
    assert ev.collected_at is not None
