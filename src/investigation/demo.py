import logging
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

from src.adapters.mock_providers import MockLogsProvider, MockMetricsProvider, MockTopologyProvider
from src.adapters.ollama import OllamaProvider
from src.domain import (
    Incident,
    InvestigationStatus,
    LogsProvider,
    MetricsProvider,
    TopologyProvider,
    TracesProvider,
)
from src.investigation.engine import InvestigationEngine
from src.investigation.tools import ToolDispatcher
from src.persistence.repository import SQLiteRepository

# Force basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def run_demo(
    use_mock_llm: bool = False,
    live_telemetry: bool = False,
    fault_scenario: str | None = None,
    evidence_strategy: str = "generic",
    use_summarizer: bool = True,
) -> None:
    print("=" * 60)
    print("Aegis Phase 2/3 - End-to-End Investigation")
    print("=" * 60)

    # 1. Initialize Persistence
    print("\n[1] Initializing SQLite Repository...")
    repository = SQLiteRepository(db_path="aegis_demo.db")

    # 2. Register Telemetry Providers
    print("[2] Registering Telemetry Providers...")
    
    metrics_provider: MetricsProvider
    logs_provider: LogsProvider
    traces_provider: TracesProvider

    if live_telemetry:
        from pathlib import Path

        import yaml

        from src.adapters.jaeger import JaegerProvider
        from src.adapters.opensearch import OpenSearchProvider
        from src.adapters.prometheus import PrometheusProvider
        
        config_path = Path(__file__).parent.parent.parent / "config" / "opentelemetry-demo.yaml"
        metrics_endpoint = "http://localhost:9090"
        logs_endpoint = "http://localhost:9200"
        traces_endpoint = "http://localhost:16686"
        
        if config_path.exists():
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
                metrics_endpoint = config.get("telemetry", {}).get("metrics", {}).get("endpoint", metrics_endpoint)
                logs_endpoint = config.get("telemetry", {}).get("logs", {}).get("endpoint", logs_endpoint)
                traces_endpoint = config.get("telemetry", {}).get("traces", {}).get("endpoint", traces_endpoint)
        
        print(f"    - Using LIVE PrometheusProvider ({metrics_endpoint})")
        metrics_provider = PrometheusProvider(endpoint=metrics_endpoint)
        
        print(f"    - Using LIVE OpenSearchProvider ({logs_endpoint})")
        logs_provider = OpenSearchProvider(endpoint=logs_endpoint)
        
        print(f"    - Using LIVE JaegerProvider ({traces_endpoint})")
        traces_provider = JaegerProvider(endpoint=traces_endpoint)
    else:
        from src.adapters.mock_providers import MockTracesProvider
        print("    - Using MOCK MockMetricsProvider")
        metrics_provider = MockMetricsProvider()
        print("    - Using MOCK MockLogsProvider")
        logs_provider = MockLogsProvider()
        print("    - Using MOCK MockTracesProvider")
        traces_provider = MockTracesProvider()
        
    topology_provider: TopologyProvider = MockTopologyProvider()

    # 3. Load fault scenario if provided
    scenario_config = None
    if fault_scenario:
        from pathlib import Path

        import yaml
        scenario_path = Path(fault_scenario)
        if scenario_path.exists():
            with open(scenario_path, "r") as f:
                scenario_config = yaml.safe_load(f)
            print(f"[3] Loaded fault scenario: {scenario_config.get('scenario_id', 'unknown')}")
        else:
            print(f"[3] WARNING: Scenario file not found: {fault_scenario}")

    # 4. Create Incident
    print("[4] Generating Incident Scenario...")
    incident_start = datetime.now(timezone.utc)
    
    if scenario_config:
        # Fault scenario — describe the SYMPTOM, not the cause
        incident = Incident(
            id=f"inc_fault_{int(datetime.now(timezone.utc).timestamp())}",
            title=scenario_config.get("incident_title", "Service Degradation Detected"),
            description=scenario_config.get("incident_description", "Users are experiencing degraded performance."),
            start_time=incident_start,
            status=InvestigationStatus.INCIDENT_CREATED,
            scenario_id=scenario_config.get("scenario_id"),
            affected_service=scenario_config.get("affected_service", "frontend"),
            ground_truth_reference=scenario_config.get("expected_root_cause"),
            evidence=[]
        )
    elif live_telemetry:
        incident = Incident(
            id=f"inc_demo_{int(datetime.now(timezone.utc).timestamp())}",
            title="Frontend Anomaly Investigation",
            description="Routine anomaly investigation for frontend service.",
            start_time=incident_start,
            status=InvestigationStatus.INCIDENT_CREATED,
            scenario_id="scenario_live_frontend_001",
            affected_service="frontend",
            evidence=[]
        )
    else:
        incident = Incident(
            id=f"inc_demo_{int(datetime.now(timezone.utc).timestamp())}",
            title="Payment API Latency Spike",
            description="Users are reporting slow payments. Checkout is delayed.",
            start_time=incident_start,
            status=InvestigationStatus.INCIDENT_CREATED,
            scenario_id="scenario_payment_api_001",
            affected_service="payment-api",
            evidence=[]
        )
    
    if live_telemetry:
        import httpx
        try:
            print("    - Generating fresh application traffic...")
            for path in ["/", "/api/products", "/api/products/OLJCESPC7Z", "/api/data/?contextKeys=telescope"]:
                try:
                    httpx.get(f"http://localhost:8080{path}", timeout=5.0)
                except Exception:
                    pass
        except Exception:
            pass

    # 5. Create Tool Dispatcher
    print("[5] Creating Tool Dispatcher...")
    tool_dispatcher = ToolDispatcher(metrics_provider, logs_provider, topology_provider, incident_start_time=incident_start, traces_provider=traces_provider)

    # 6. Initialize LLM Provider
    llm_provider: Any
    if use_mock_llm:
        print("[6] Initializing Mock LLM Provider...")
        from tests.mock_llm import MockLLMProvider

        llm_provider = MockLLMProvider()
    else:
        llm_provider = OllamaProvider()
        print(f"[6] Initializing Ollama Provider ({llm_provider.model})...")
        status = llm_provider.get_status()
        if status.health.value != "HEALTHY":
            print(f"ERROR: Ollama is not healthy or model {llm_provider.model} is missing: {status.limitations}")
            print(f"Please run Ollama locally with {llm_provider.model} or run with --mock-llm")
            sys.exit(1)

    # 7. Run Investigation
    print(f"[7] Starting Investigation Engine (strategy: {evidence_strategy}, summarizer: {use_summarizer})...\n")
    engine = InvestigationEngine(
        incident=incident,
        llm_provider=llm_provider,
        tool_dispatcher=tool_dispatcher,
        repository=repository,
        evidence_strategy=evidence_strategy,
        use_summarizer=use_summarizer,
    )

    engine.run()

    # 8. Output Results
    print("\n" + "="*60)
    print("Investigation Complete")
    print("="*60)
    print(f"Incident ID      : {engine.incident.id}")
    print(f"Final Status     : {engine.incident.status.value}")
    print(f"Duration         : {engine.incident.end_time - engine.incident.start_time if engine.incident.end_time else 'Unknown'}")
    print(f"Tool Calls       : {engine.tool_call_count}")
    print(f"LLM Turns        : {engine.turn_count}")
    
    metric_count = sum(1 for e in engine.incident.evidence if e.type.value == "METRIC")
    log_count = sum(1 for e in engine.incident.evidence if e.type.value == "LOG")
    trace_count = sum(1 for e in engine.incident.evidence if e.type.value == "TRACE")
    print(f"\nTotal Evidence   : {len(engine.incident.evidence)} (Metrics: {metric_count}, Logs: {log_count}, Traces: {trace_count})")
    
    if engine.provider_errors:
        print(f"Provider Errors  : {len(engine.provider_errors)}")
        for err in engine.provider_errors:
            print(f"  - {err}")
    
    print("\n-- Hypotheses Generated --")
    for h in engine.incident.hypotheses:
        print(f" - {h.description}")
        
    diagnosis_event = None
    rec_event = None
    with sqlite3.connect(repository.db_path) as conn:
        cursor = conn.execute("SELECT summary FROM events WHERE action = 'DIAGNOSIS_PRODUCED' AND incident_id = ?", (engine.incident.id,))
        row = cursor.fetchone()
        if row: diagnosis_event = row[0]
        
        cursor = conn.execute("SELECT action, reason FROM recommendations WHERE incident_id = ?", (engine.incident.id,))
        r_row = cursor.fetchone()
        if r_row: rec_event = f"{r_row[0]} (Reason: {r_row[1]})"
            
    classification = engine.incident.diagnosis_classification
    print(f"\nClassification   : {classification.value if classification else 'None'}")
    print(f"Diagnosis        : {diagnosis_event or engine.incident.diagnosis or 'None produced'}")
    print(f"Recommendation   : {rec_event or 'None produced'}")
    
    if scenario_config and engine.incident.ground_truth_reference:
        print(f"\nGround Truth     : {engine.incident.ground_truth_reference}")
    
    print("="*60)


if __name__ == "__main__":
    use_mock = "--mock-llm" in sys.argv
    live_telemetry = "--live-telemetry" in sys.argv
    use_summarizer = "--no-summarizer" not in sys.argv
    
    evidence_strategy = "generic"
    fault_scenario = None
    for i, arg in enumerate(sys.argv):
        if arg == "--fault-scenario" and i + 1 < len(sys.argv):
            fault_scenario = sys.argv[i + 1]
        elif arg == "--evidence-strategy" and i + 1 < len(sys.argv):
            evidence_strategy = sys.argv[i + 1]
    
    run_demo(
        use_mock_llm=use_mock,
        live_telemetry=live_telemetry,
        fault_scenario=fault_scenario,
        evidence_strategy=evidence_strategy,
        use_summarizer=use_summarizer,
    )
