from datetime import datetime, timezone, timedelta
import yaml
from pathlib import Path
from src.adapters.jaeger import JaegerProvider
from src.adapters.mock_providers import MockMetricsProvider, MockLogsProvider, MockTopologyProvider
from src.investigation.tools import ToolDispatcher
from src.domain import ToolCall, HealthStatus

def main():
    print("=" * 60)
    print("Phase 2D - Live JaegerProvider Verification")
    print("=" * 60)

    config_path = Path(__file__).parent.parent / "config" / "opentelemetry-demo.yaml"
    endpoint = "http://localhost:16686"
    if config_path.exists():
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            endpoint = config.get("telemetry", {}).get("traces", {}).get("endpoint", endpoint)
            
    print(f"Connecting to Jaeger at {endpoint}...")
    
    metrics = MockMetricsProvider()
    logs = MockLogsProvider()
    traces = JaegerProvider(endpoint=endpoint)
    topo = MockTopologyProvider()
    
    status = traces.get_status()
    print(f"Backend Status: {status.health.value}")
    if status.health == HealthStatus.UNAVAILABLE:
        print("FAILED: Jaeger is unavailable")
        exit(1)
    
    dispatcher = ToolDispatcher(
        metrics, 
        logs, 
        topo, 
        incident_start_time=datetime.now(timezone.utc) - timedelta(hours=1),
        traces_provider=traces
    )
    
    # Fake a ToolCall that queries Jaeger
    call = ToolCall(
        id="call_003",
        name="search_traces",
        arguments={"service_name": "frontend"}
    )
    
    print(f"Dispatching ToolCall: {call.name} with service_name: {call.arguments['service_name']}")
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
