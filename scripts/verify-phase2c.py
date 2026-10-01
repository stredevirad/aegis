from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from src.adapters.mock_providers import MockMetricsProvider, MockTopologyProvider
from src.adapters.opensearch import OpenSearchProvider
from src.domain import ToolCall
from src.investigation.tools import ToolDispatcher


def main():
    print("=" * 60)
    print("Phase 2C - Live OpenSearchProvider Verification")
    print("=" * 60)

    config_path = Path(__file__).parent.parent / "config" / "opentelemetry-demo.yaml"
    endpoint = "http://localhost:9200"
    if config_path.exists():
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            endpoint = config.get("telemetry", {}).get("logs", {}).get("endpoint", endpoint)
            
    print(f"Connecting to OpenSearch at {endpoint}...")
    
    metrics = MockMetricsProvider()
    logs = OpenSearchProvider(endpoint=endpoint)
    topo = MockTopologyProvider()
    
    dispatcher = ToolDispatcher(
        metrics, 
        logs, 
        topo, 
        incident_start_time=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    
    # Fake a ToolCall that queries OpenSearch
    call = ToolCall(
        id="call_002",
        name="search_logs",
        arguments={"query": "frontend"}
    )
    
    print(f"Dispatching ToolCall: {call.name} with query: {call.arguments['query']}")
    result = dispatcher.dispatch(call)
    
    if isinstance(result, str):
        print(f"FAILED: {result}")
        exit(1)
        
    print(f"\nSuccess! ToolResult returned {len(result.evidence)} Evidence objects.")
    if result.evidence:
        ev = result.evidence[0]
        print(f"Sample Evidence ID: {ev.id}")
        print(f"Type: {ev.type}")
        print(f"Source: {ev.source_provider}")
        print(f"Service Resource: {ev.service_resource}")
        print(f"Summary: {ev.summary}")
    
    print("=" * 60)

if __name__ == "__main__":
    main()
