import logging
import sys
from datetime import datetime, timezone

from src.adapters.mock_providers import MockLogsProvider, MockMetricsProvider, MockTopologyProvider
from src.adapters.ollama import OllamaProvider
from src.domain import Incident, InvestigationStatus
from src.investigation.engine import InvestigationEngine
from src.investigation.tools import ToolDispatcher
from src.persistence.repository import SQLiteRepository

# Force basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
from typing import Any


def run_demo(use_mock_llm: bool = False) -> None:
    print("=" * 60)
    print("Aegis Phase 1 - End-to-End Deterministic Investigation")
    print("=" * 60)

    # 1. Initialize Persistence
    print("\n[1] Initializing SQLite Repository...")
    repository = SQLiteRepository(db_path="aegis_demo.db")

    # 2. Register Mock Telemetry Providers
    print("[2] Registering Mock Telemetry Providers...")
    metrics_provider = MockMetricsProvider()
    logs_provider = MockLogsProvider()
    topology_provider = MockTopologyProvider()

    # 5. Create Fake Incident
    print("[5] Generating Fake Incident Scenario...")
    incident_start = datetime.now(timezone.utc)
    incident = Incident(
        id=f"inc_demo_{int(datetime.now(timezone.utc).timestamp())}",
        title="Payment API Latency Spike",
        description="Users are reporting slow payments. Checkout is delayed.",
        start_time=incident_start,
        status=InvestigationStatus.INCIDENT_CREATED,
        scenario_id="scenario_payment_api_001",
        affected_service="payment-api"
    )

    # 3. Create Tool Dispatcher
    print("[3] Creating Tool Dispatcher...")
    tool_dispatcher = ToolDispatcher(metrics_provider, logs_provider, topology_provider, incident_start_time=incident_start)

    # 4. Initialize LLM Provider
    llm_provider: Any
    if use_mock_llm:
        print("[4] Initializing Mock LLM Provider...")
        from tests.mock_llm import MockLLMProvider

        llm_provider = MockLLMProvider()
    else:
        llm_provider = OllamaProvider()
        print(f"[4] Initializing Ollama Provider ({llm_provider.model})...")
        status = llm_provider.get_status()
        if status.health.value != "HEALTHY":
            print(f"ERROR: Ollama is not healthy or model {llm_provider.model} is missing: {status.limitations}")
            print(f"Please run Ollama locally with {llm_provider.model} or run with --mock-llm")
            sys.exit(1)

    # 6. Run Investigation
    print("[6] Starting Investigation Engine...\n")
    engine = InvestigationEngine(
        incident=incident,
        llm_provider=llm_provider,
        tool_dispatcher=tool_dispatcher,
        repository=repository
    )

    engine.run()

    # 7. Output Results
    print("\n" + "="*60)
    print("Investigation Complete")
    print("="*60)
    print(f"Incident ID : {engine.incident.id}")
    print(f"Final Status: {engine.incident.status.value}")
    print(f"Duration    : {engine.incident.end_time - engine.incident.start_time if engine.incident.end_time else 'Unknown'}")
    print(f"Tool Calls  : {engine.tool_call_count}")
    print(f"LLM Turns   : {engine.turn_count}")
    
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
            
    print(f"\nDiagnosis     : {diagnosis_event or 'None produced'}")
    print(f"Recommendation: {rec_event or 'None produced'}")
    print("="*60)


if __name__ == "__main__":
    import sqlite3

    use_mock = "--mock-llm" in sys.argv
    run_demo(use_mock_llm=use_mock)
