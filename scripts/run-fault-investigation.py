"""End-to-end fault injection investigation orchestrator.

Usage:
    python scripts/run-fault-investigation.py [--scenario config/scenarios/product-catalog-failure.yaml] [--runs 2]

Workflow:
    1. Ensure all faults OFF
    2. Verify healthy baseline
    3. Generate baseline traffic
    4. Collect baseline telemetry signature
    5. Inject chosen fault
    6. Wait for propagation
    7. Generate targeted fault-path traffic
    8. Collect fault telemetry
    9. Prove telemetry changed (baseline vs fault comparison)
    10. Run Aegis investigation
    11. Evaluate RCA with evidence provenance
    12. Recover fault
    13. Verify recovery
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "opentelemetry-demo.yaml"
    if not config_path.exists():
        log(f"ERROR: Config not found: {config_path}")
        sys.exit(1)
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def verify_health(config: dict) -> bool:
    """Verify all backends are reachable."""
    endpoints = {
        "Application": "http://localhost:8080",
        "Prometheus": config["telemetry"]["metrics"]["endpoint"] + "/-/healthy",
        "OpenSearch": config["telemetry"]["logs"]["endpoint"] + "/_cluster/health",
        "Jaeger": config["telemetry"]["traces"]["endpoint"] + "/jaeger/ui/api/services",
    }
    all_ok = True
    for name, url in endpoints.items():
        try:
            r = httpx.get(url, timeout=5.0)
            if r.status_code == 200:
                log(f"  [OK] {name}: OK")
            else:
                log(f"  [FAIL] {name}: HTTP {r.status_code}")
                all_ok = False
        except Exception as e:
            log(f"  [FAIL] {name}: {e}")
            all_ok = False
    return all_ok


def generate_traffic(count: int = 10) -> list[int]:
    """Generate application traffic across targeted pathways and return status codes."""
    log(f"Generating {count} requests across application and product pathways...")
    endpoints = [
        "http://localhost:8080",
        "http://localhost:8080/api/products",
        "http://localhost:8080/api/products/OLJCESPC7Z",
        "http://localhost:8080/api/data/?contextKeys=telescope",
    ]
    statuses = []
    success = 0
    for i in range(count):
        url = endpoints[i % len(endpoints)]
        try:
            r = httpx.get(url, timeout=5.0)
            statuses.append(r.status_code)
            if r.status_code == 200:
                success += 1
        except Exception:
            statuses.append(504)
        time.sleep(0.2)
    log(f"  {success}/{count} requests returned 200 OK (Statuses: {statuses[:8]}...)")
    return statuses


def recover_all_faults() -> None:
    """Ensure all feature flags are restored to default off state."""
    script = Path(__file__).parent / "inject-fault.py"
    subprocess.run([sys.executable, str(script), "--recover-all"], check=True)


def inject_fault(flag_name: str, value: str) -> None:
    """Inject a fault via inject-fault.py."""
    script = Path(__file__).parent / "inject-fault.py"
    subprocess.run([sys.executable, str(script), "--flag", flag_name, "--value", value], check=True)


def recover_fault(flag_name: str) -> None:
    """Recover a fault via inject-fault.py."""
    script = Path(__file__).parent / "inject-fault.py"
    subprocess.run([sys.executable, str(script), "--flag", flag_name, "--recover"], check=True)


def collect_telemetry_signature(config: dict, target_service: str, affected_service: str) -> dict[str, Any]:
    """Collect quantified telemetry signature (metrics, error logs, error traces)."""
    signature = {
        "metrics_series": 0,
        "error_logs_count": 0,
        "error_traces_count": 0,
        "sample_error_log": None,
        "sample_error_trace": None,
    }
    prom_ep = config["telemetry"]["metrics"]["endpoint"]
    os_ep = config["telemetry"]["logs"]["endpoint"]
    jaeger_ep = config["telemetry"]["traces"]["endpoint"]

    # 1. Metrics query
    try:
        query = f'http_server_request_duration_seconds_count{{service_name=~".*({affected_service}|{target_service}).*"}}'
        r = httpx.get(f"{prom_ep}/api/v1/query", params={"query": query}, timeout=5.0)
        if r.status_code == 200:
            results = r.json().get("data", {}).get("result", [])
            signature["metrics_series"] = len(results)
    except Exception as e:
        log(f"  Warning: Metrics signature check failed: {e}")

    # 2. OpenSearch error logs query (past 3m)
    try:
        payload = {
            "size": 5,
            "query": {
                "bool": {
                    "must": [{"query_string": {"query": f"({target_service} OR {affected_service}) AND (error OR ERROR OR fail OR Exception OR 500)"}}],
                    "filter": [{"range": {"@timestamp": {"gte": "now-3m"}}}]
                }
            }
        }
        r = httpx.post(f"{os_ep}/otel-logs-*/_search", json=payload, timeout=5.0)
        if r.status_code == 200:
            hits_data = r.json().get("hits", {})
            signature["error_logs_count"] = hits_data.get("total", {}).get("value", 0)
            hits = hits_data.get("hits", [])
            if hits:
                signature["sample_error_log"] = hits[0].get("_source", {}).get("body", "")
    except Exception as e:
        log(f"  Warning: Logs signature check failed: {e}")

    # 3. Jaeger error traces query (past 3m)
    try:
        for svc in (target_service, affected_service):
            r = httpx.get(f"{jaeger_ep}/jaeger/ui/api/traces", params={"service": svc, "limit": 10}, timeout=5.0)
            if r.status_code == 200:
                traces = r.json().get("data", [])
                err_traces = [
                    t for t in traces
                    if any(
                        (tag.get("key") == "error" and tag.get("value") is True) or
                        (tag.get("key") == "otel.status_code" and str(tag.get("value")).upper() == "ERROR")
                        for sp in t.get("spans", [])
                        for tag in sp.get("tags", [])
                    )
                ]
                signature["error_traces_count"] += len(err_traces)
                if err_traces and not signature["sample_error_trace"]:
                    signature["sample_error_trace"] = err_traces[0].get("traceID")
    except Exception as e:
        log(f"  Warning: Traces signature check failed: {e}")

    return signature


def run_investigation(
    scenario_path: str,
    evidence_strategy: str = "generic",
    use_summarizer: bool = True,
) -> dict:
    """Run Aegis demo with fault scenario."""
    env = os.environ.copy()
    env["AEGIS_INVESTIGATION_MODE"] = "fast"
    env["OLLAMA_MODEL"] = "qwen3.5:4b"
    env["OLLAMA_THINK"] = "false"

    cmd = [
        sys.executable, "-m", "src.investigation.demo",
        "--live-telemetry",
        "--fault-scenario", scenario_path,
        "--evidence-strategy", evidence_strategy,
    ]
    if not use_summarizer:
        cmd.append("--no-summarizer")

    log(f"Running Aegis investigation (strategy: {evidence_strategy}, summarizer: {use_summarizer})...")
    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    duration = time.time() - start

    output = result.stdout
    print(output)
    if result.returncode != 0:
        log(f"WARNING: Investigation exited with code {result.returncode}")
        if result.stderr:
            print(result.stderr[-500:])

    return {"output": output, "duration": duration, "returncode": result.returncode}


def parse_investigation_output(output: str) -> dict[str, Any]:
    """Parse key fields from investigation output."""
    parsed: dict[str, Any] = {}
    for line in output.split("\n"):
        line = line.strip()
        if line.startswith("Incident ID"):
            parsed["incident_id"] = line.split(":")[-1].strip()
        elif line.startswith("Final Status"):
            parsed["status"] = line.split(":")[-1].strip()
        elif line.startswith("Classification"):
            parsed["classification"] = line.split(":")[-1].strip()
        elif line.startswith("Diagnosis"):
            parsed["diagnosis"] = line.split(":", 1)[-1].strip()
        elif line.startswith("Total Evidence"):
            parsed["evidence"] = line.split(":", 1)[-1].strip()
        elif line.startswith("Tool Calls"):
            parsed["tool_calls"] = line.split(":")[-1].strip()
        elif line.startswith("LLM Turns"):
            parsed["llm_turns"] = line.split(":")[-1].strip()
        elif line.startswith("Duration"):
            parsed["duration"] = line.split(":", 1)[-1].strip()
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fault injection investigation")
    parser.add_argument("--scenario", type=str, default="config/scenarios/product-catalog-failure.yaml",
                        help="Path to scenario YAML")
    parser.add_argument("--runs", type=int, default=2, help="Number of investigation runs")
    parser.add_argument("--evidence-strategy", type=str, default="generic", choices=["generic", "error_focused"],
                        help="Initial evidence strategy (generic or error_focused)")
    parser.add_argument("--no-summarizer", action="store_true",
                        help="Disable structured evidence summarizer (send raw bounded evidence)")
    args = parser.parse_args()

    scenario_path = Path(args.scenario)
    if not scenario_path.exists():
        log(f"ERROR: Scenario not found: {scenario_path}")
        sys.exit(1)

    with open(scenario_path, "r") as f:
        scenario = yaml.safe_load(f)

    flag_name = scenario["flag_name"]
    flag_value = scenario["flag_value"]
    target_service = scenario.get("target", "product-catalog")
    affected_service = scenario.get("affected_service", "frontend")
    ground_truth = scenario.get("expected_root_cause", "")

    config = load_config()
    run_results = []

    print("=" * 70)
    print("Aegis Phase 2E-2 - Controlled Fault Investigation")
    print("=" * 70)
    print(f"Scenario      : {scenario.get('scenario_id')}")
    print(f"Fault Flag    : {flag_name} -> {flag_value}")
    print(f"Target Service: {target_service}")
    print(f"Affected Svc  : {affected_service}")
    print(f"Ground Truth  : {ground_truth}")
    print(f"Planned Runs  : {args.runs}")
    print("=" * 70)

    # Step 1: Ensure all faults OFF
    log("Step 1: Restoring all feature flags to defaults (all faults OFF)...")
    recover_all_faults()
    time.sleep(3)

    # Step 2: Verify baseline health
    log("Step 2: Verifying baseline health...")
    if not verify_health(config):
        log("ERROR: Baseline health check failed. Aborting.")
        sys.exit(1)
    log("Baseline health: OK")

    # Step 3: Generate baseline traffic
    log("Step 3: Generating baseline application traffic...")
    generate_traffic(10)
    time.sleep(2)

    # Step 4: Collect baseline telemetry signature
    log("Step 4: Collecting baseline telemetry signature...")
    baseline_sig = collect_telemetry_signature(config, target_service, affected_service)
    log(f"  Baseline Error Logs Count  : {baseline_sig['error_logs_count']}")
    log(f"  Baseline Error Traces Count: {baseline_sig['error_traces_count']}")
    log(f"  Baseline Metric Series     : {baseline_sig['metrics_series']}")

    # Step 5: Inject fault
    log(f"\nStep 5: Injecting fault ({flag_name} -> {flag_value})...")
    inject_fault(flag_name, flag_value)

    # Step 6: Propagation delay
    log("Step 6: Waiting 15s for fault propagation...")
    time.sleep(15)

    # Step 7: Generate targeted fault-path traffic
    log("Step 7: Generating targeted fault-path traffic...")
    fault_statuses = generate_traffic(15)
    time.sleep(3)

    # Step 8 & 9: Collect fault telemetry & prove telemetry changed
    log("Step 8 & 9: Collecting fault telemetry signature and verifying delta...")
    fault_sig = collect_telemetry_signature(config, target_service, affected_service)
    log(f"  Fault Error Logs Count  : {fault_sig['error_logs_count']} (Delta: +{fault_sig['error_logs_count'] - baseline_sig['error_logs_count']})")
    log(f"  Fault Error Traces Count: {fault_sig['error_traces_count']} (Delta: +{fault_sig['error_traces_count'] - baseline_sig['error_traces_count']})")
    log(f"  Fault Metric Series     : {fault_sig['metrics_series']}")

    telemetry_changed = (
        fault_sig["error_traces_count"] > baseline_sig["error_traces_count"] or
        fault_sig["error_logs_count"] > baseline_sig["error_logs_count"] or
        any(s >= 500 for s in fault_statuses)
    )

    if telemetry_changed:
        log("  [VERIFIED] Telemetry signature changed significantly under fault condition.")
    else:
        log("  [WARNING] Telemetry delta did not show significant change. Continuing investigation.")

    # Step 10 & 11: Run investigations and evaluate
    from src.evaluation.rca_evaluator import evaluate_rca
    from src.persistence.repository import SQLiteRepository

    repo = SQLiteRepository(db_path="aegis_demo.db")

    for run_num in range(1, args.runs + 1):
        log(f"\n{'='*50}")
        log(f"Step 10.{run_num}: Running investigation (run {run_num}/{args.runs})...")
        log(f"{'='*50}")

        inv_result = run_investigation(
            str(scenario_path),
            evidence_strategy=args.evidence_strategy,
            use_summarizer=not args.no_summarizer,
        )
        parsed = parse_investigation_output(inv_result["output"])
        incident_id = parsed.get("incident_id")

        # Load persisted incident and recommendations from SQLite for evidence-backed evaluation
        loaded_incident = repo.load_incident(incident_id) if incident_id else None
        loaded_recs = repo.load_recommendations(incident_id) if incident_id else []

        cited_evidence_ids = []
        if loaded_recs:
            cited_evidence_ids = loaded_recs[0].supporting_evidence_ids
        elif loaded_incident and loaded_incident.hypotheses:
            for h in loaded_incident.hypotheses:
                cited_evidence_ids.extend(h.supporting_evidence_ids)

        rca_eval = evaluate_rca(
            ground_truth=ground_truth,
            model_diagnosis=parsed.get("diagnosis"),
            classification=parsed.get("classification"),
            scenario_id=scenario.get("scenario_id", ""),
            target_service=target_service,
            supporting_evidence_ids=cited_evidence_ids,
            available_evidence=loaded_incident.evidence if loaded_incident else None,
            incident_title=scenario.get("incident_title", ""),
            incident_description=scenario.get("incident_description", ""),
        )

        run_data = {
            "run": run_num,
            "incident_id": incident_id,
            "status": parsed.get("status"),
            "classification": parsed.get("classification"),
            "diagnosis": parsed.get("diagnosis"),
            "evidence_total": len(loaded_incident.evidence) if loaded_incident else 0,
            "tool_calls": parsed.get("tool_calls"),
            "llm_turns": parsed.get("llm_turns"),
            "duration": inv_result["duration"],
            "rca_result": rca_eval.rca_result.value,
            "valid_evidence_count": rca_eval.valid_evidence_count,
            "modalities_supported": rca_eval.modalities_supported,
            "incident_leak_ratio": rca_eval.incident_leak_ratio,
            "causal_terms": rca_eval.causal_terms_found,
            "novel_content_ratio": rca_eval.novel_content_ratio,
            "notes": rca_eval.notes,
        }
        run_results.append(run_data)

        log(f"\n--- Run {run_num} Evaluation ---")
        log(f"  Incident ID   : {run_data['incident_id']}")
        log(f"  Status        : {run_data['status']}")
        log(f"  Classification: {run_data['classification']}")
        log(f"  Diagnosis     : {run_data['diagnosis']}")
        log(f"  RCA Result    : {rca_eval.rca_result.value}")
        log(f"  Evidence Valid: {rca_eval.valid_evidence_count} items")
        log(f"  Modalities    : {rca_eval.modalities_supported}")
        log(f"  Incident Leak : {rca_eval.incident_leak_ratio:.0%}")
        log(f"  Novel Content : {rca_eval.novel_content_ratio:.0%}")
        log(f"  Causal Terms  : {rca_eval.causal_terms_found or 'None'}")
        log(f"  Duration      : {inv_result['duration']:.1f}s")
        if rca_eval.notes:
            log(f"  Notes         : {rca_eval.notes}")

        if run_num < args.runs:
            log("Generating fresh traffic and waiting 10s between runs...")
            generate_traffic(10)
            time.sleep(10)

    # Step 12: Recover fault
    log("\nStep 12: Recovering fault...")
    recover_fault(flag_name)

    # Step 13: Wait and verify recovery health
    log("Step 13: Waiting 15s for recovery propagation...")
    time.sleep(15)
    log("Verifying recovery health...")
    verify_health(config)

    # Final Report
    print("\n" + "=" * 70)
    print("FAULT INVESTIGATION REPORT")
    print("=" * 70)
    print(f"Scenario      : {scenario.get('scenario_id')}")
    print(f"Fault Flag    : {flag_name}")
    print(f"Ground Truth  : {ground_truth}")
    print(f"Total Runs    : {len(run_results)}")
    print()
    for r in run_results:
        print(f"  Run {r['run']}: {r['rca_result']} | {r['classification']} | {r['diagnosis'][:60] if r.get('diagnosis') else 'None'}...")
        if r.get("notes"):
            print(f"         Notes: {r['notes']}")

    correct_count = sum(1 for r in run_results if r["rca_result"] in ("CORRECT", "PARTIALLY_CORRECT"))
    print(f"\nConsistency   : {correct_count}/{len(run_results)} runs produced correct/partially-correct diagnosis")
    print("=" * 70)


if __name__ == "__main__":
    main()
