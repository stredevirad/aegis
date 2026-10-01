"""Phase 5: Replay-Based Model Capability Study Runner.

Evaluates multiple local LLM models on identical, deterministic Evidence snapshots
to disentangle Aegis architectural capabilities from individual LLM reasoning limits.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.ollama import OllamaProvider
from src.domain import (
    Incident,
    InvestigationStatus,
    LLMMessage,
    LLMRequest,
    MessageRole,
)
from src.evaluation.rca_evaluator import RCAEvaluation, evaluate_rca
from src.investigation.evidence_summarizer import (
    format_evidence_summary_for_llm,
    summarize_evidence,
)
from src.persistence.replay import load_snapshot, reconstruct_evidence


def run_single_model_replay(
    model_name: str,
    snapshot_path: str | Path,
    ollama_url: str = "http://localhost:11434",
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Run an offline replay investigation for a specific model against an Evidence snapshot."""
    snapshot = load_snapshot(snapshot_path)
    evidence_list = reconstruct_evidence(snapshot)

    # Reconstruct Incident entity
    incident = Incident(
        id=f"inc_replay_{snapshot.snapshot_id}",
        title=f"Replay Incident: {snapshot.scenario_id}",
        description=f"Automated offline replay against scenario {snapshot.scenario_id}",
        start_time=datetime.now(timezone.utc),
        status=InvestigationStatus.COLLECTING_EVIDENCE,
        scenario_id=snapshot.scenario_id,
        affected_service=snapshot.affected_service,
        evidence=evidence_list,
    )

    ev_summary = summarize_evidence(evidence_list)
    formatted_evidence = format_evidence_summary_for_llm(ev_summary)

    system_prompt = (
        "You are Aegis, an expert Site Reliability Engineer performing Root Cause Analysis (RCA).\n"
        "Analyze the provided multi-modal telemetry evidence (metrics, logs, traces) and determine:\n"
        "1. The root cause service/component.\n"
        "2. The exact causal mechanism explaining why the failure occurred.\n"
        "3. Provide a concise root cause diagnosis."
    )

    user_prompt = (
        f"Incident: {incident.title}\nAffected Service: {incident.affected_service}\n\n"
        f"Collected Telemetry Evidence:\n{formatted_evidence}\n\n"
        "Formulate your final root cause diagnosis explaining the failure mechanism."
    )

    # Execute LLM inference via Ollama
    llm = OllamaProvider(model=model_name, base_url=ollama_url)
    req = LLMRequest(
        messages=[
            LLMMessage(role=MessageRole.SYSTEM, content=system_prompt),
            LLMMessage(role=MessageRole.USER, content=user_prompt),
        ]
    )
    
    t0 = time.perf_counter()
    try:
        resp = llm.generate(req)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        diagnosis = resp.content or ""
    except Exception as exc:
        dt_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "model": model_name,
            "status": "error",
            "error": str(exc),
            "latency_ms": round(dt_ms, 2),
        }

    # Evaluate diagnosis against ground truth with evaluate_rca
    ground_truth = snapshot.metadata.get("ground_truth_root_cause", "")
    target_component = snapshot.metadata.get("ground_truth_component", snapshot.target_service)
    
    eval_result: RCAEvaluation = evaluate_rca(
        ground_truth=ground_truth,
        model_diagnosis=diagnosis,
        classification="PLAUSIBLE",
        scenario_id=snapshot.scenario_id,
        target_service=target_component,
        available_evidence=evidence_list,
        incident_title=incident.title,
        incident_description=incident.description,
    )

    component_localized = target_component.lower() in diagnosis.lower() if target_component else True

    return {
        "model": model_name,
        "status": "success",
        "snapshot_id": snapshot.snapshot_id,
        "scenario_id": snapshot.scenario_id,
        "latency_ms": round(dt_ms, 2),
        "diagnosis": diagnosis,
        "classification": eval_result.rca_result.value,
        "component_localized": component_localized,
        "incident_leak_ratio": round(eval_result.incident_leak_ratio, 3),
        "causal_terms_found": eval_result.causal_terms_found,
        "evaluation_notes": eval_result.notes,
    }


def run_multi_model_study(
    models: list[str],
    snapshots: list[str | Path],
    ollama_url: str = "http://localhost:11434",
) -> dict[str, Any]:
    """Execute the multi-model comparison study across available snapshots."""
    study_results: dict[str, list[dict[str, Any]]] = {}

    for model in models:
        study_results[model] = []
        for snap_path in snapshots:
            print(f"  [Replay] Evaluating {model} on {Path(snap_path).name}...")
            res = run_single_model_replay(
                model_name=model,
                snapshot_path=snap_path,
                ollama_url=ollama_url,
            )
            study_results[model].append(res)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "models_evaluated": models,
        "snapshots_evaluated": [str(Path(p).name) for p in snapshots],
        "results": study_results,
    }


if __name__ == "__main__":
    snapshot_dir = Path(__file__).resolve().parent.parent / "eval" / "snapshots"
    snapshots = list(snapshot_dir.glob("*.json"))
    
    # Models to test sequentially
    models_to_test = ["qwen3.5:4b", "llama3.2:1b"]

    print(f"Starting Phase 5 Model Study on {len(snapshots)} snapshots across models: {models_to_test}...")
    output = run_multi_model_study(models=models_to_test, snapshots=snapshots)
    
    out_dir = Path(__file__).resolve().parent.parent / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "model_comparison_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"Results saved to {out_file}")
