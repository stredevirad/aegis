"""Tests for Phase 5 Replay-Based Model Capability Study.

Validates:
- Loading and schema validation of curated benchmark snapshots
- Reconstruction of Evidence items and metadata
- Deterministic offline replay execution with mock LLM and evaluator
"""
from pathlib import Path

from src.domain import EvidenceType
from src.evaluation.rca_evaluator import RCAResult, evaluate_rca
from src.investigation.evidence_summarizer import (
    format_evidence_summary_for_llm,
    summarize_evidence,
)
from src.persistence.replay import load_snapshot, reconstruct_evidence


class TestSnapshotLoadingAndReconstruction:
    """Test curated benchmark snapshots."""

    def test_load_curated_snapshots(self) -> None:
        """Verify all curated benchmark snapshots in eval/snapshots/ are valid and non-empty."""
        snap_dir = Path(__file__).parent.parent / "eval" / "snapshots"
        snapshots = list(snap_dir.glob("*.json"))
        assert len(snapshots) >= 2, f"Expected at least 2 curated snapshots, found {len(snapshots)}"

        for snap_path in snapshots:
            snap = load_snapshot(snap_path)
            assert snap.snapshot_id is not None
            assert snap.scenario_id is not None
            assert len(snap.evidence_items) >= 2
            assert "ground_truth_root_cause" in snap.metadata
            assert "ground_truth_component" in snap.metadata

            # Reconstruct domain Evidence objects
            evidence_list = reconstruct_evidence(snap)
            assert len(evidence_list) == len(snap.evidence_items)
            for ev in evidence_list:
                assert ev.id is not None
                assert ev.type in (EvidenceType.METRIC, EvidenceType.LOG, EvidenceType.TRACE, EvidenceType.TOPOLOGY)
                assert ev.observed_at is not None
                assert ev.collected_at is not None

    def test_replay_with_summarizer_and_evaluator(self) -> None:
        """Verify end-to-end replay pipeline with EvidenceSummarizer and RCAEvaluator."""
        snap_path = Path(__file__).parent.parent / "eval" / "snapshots" / "snapshot_payment_db_latency.json"
        snap = load_snapshot(snap_path)
        evidence_list = reconstruct_evidence(snap)

        # Summarize
        ev_summary = summarize_evidence(evidence_list)
        formatted_prompt = format_evidence_summary_for_llm(ev_summary)
        assert "ERROR SIGNALS" in formatted_prompt
        assert "payment-api" in formatted_prompt

        # Test deterministic mock diagnosis
        mock_diagnosis = "The root cause is high query execution latency spike in downstream-db database causing payment-api timeouts and cascading frontend degradation."
        ground_truth = snap.metadata.get("ground_truth_root_cause", "")
        target_component = snap.metadata.get("ground_truth_component", "")

        eval_res = evaluate_rca(
            ground_truth=ground_truth,
            model_diagnosis=mock_diagnosis,
            classification="PLAUSIBLE",
            scenario_id=snap.scenario_id,
            target_service=target_component,
            available_evidence=evidence_list,
        )

        assert eval_res.rca_result == RCAResult.CORRECT
        assert eval_res.incident_leak_ratio < 0.2
        assert len(eval_res.causal_terms_found) > 0
