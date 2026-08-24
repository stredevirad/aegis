from datetime import datetime, timezone
import yaml
from pathlib import Path
from src.adapters.prometheus import PrometheusProvider
from src.adapters.mock_providers import MockLogsProvider, MockTopologyProvider
from src.investigation.tools import ToolDispatcher
from src.domain import ToolCall

def main():
    print("=" * 60)
    print("Phase 2B - Live ToolDispatcher Verification")
    print("=" * 60)

    config_path = Path(__file__).parent.parent / "config" / "opentelemetry-demo.yaml"
    endpoint = "http://localhost:9090"
    if config_path.exists():
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            endpoint = config.get("telemetry", {}).get("metrics", {}).get("endpoint", endpoint)
            
    print(f"Connecting to {endpoint}...")
    
    metrics = PrometheusProvider(endpoint=endpoint)
    logs = MockLogsProvider()
    topo = MockTopologyProvider()
    
    dispatcher = ToolDispatcher(metrics, logs, topo, incident_start_time=datetime.now(timezone.utc))
    
    # Fake a ToolCall that contains actual PromQL
    call = ToolCall(
        id="call_001",
        name="query_metrics",
        arguments={"query": "http_server_request_duration_seconds_count"}
    )
    
    print(f"Dispatching ToolCall: {call.name} with PromQL: {call.arguments['query']}")
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
