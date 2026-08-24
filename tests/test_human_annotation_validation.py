"""Validation of RCA Evaluator against Human-Annotated Benchmark.

Validates the Phase 3.1 requirement:
The RCA evaluator must be validated against human annotation of >=10 diagnoses,
measuring Observed Agreement and Cohen's Kappa.
"""
import json
from pathlib import Path
from typing import Any

from src.evaluation.rca_evaluator import evaluate_rca


def compute_cohens_kappa(human_labels: list[str], evaluator_labels: list[str]) -> tuple[float, float]:
    """Compute Observed Agreement and Cohen's Kappa between two raters.
    
    Returns:
        (observed_agreement, kappa)
    """
    assert len(human_labels) == len(evaluator_labels)
    n = len(human_labels)
    if n == 0:
        return 1.0, 1.0

    categories = sorted(list(set(human_labels) | set(evaluator_labels)))
    
    # Observed agreement P_o
    agreements = sum(1 for h, e in zip(human_labels, evaluator_labels) if h == e)
    p_o = agreements / n

    # Expected agreement by chance P_e
    p_e = 0.0
    for cat in categories:
        p_human = sum(1 for h in human_labels if h == cat) / n
        p_eval = sum(1 for e in evaluator_labels if e == cat) / n
        p_e += p_human * p_eval

    if p_e >= 1.0:
        kappa = 1.0
    else:
        kappa = (p_o - p_e) / (1.0 - p_e)

    return p_o, kappa


def load_annotation_fixture() -> list[dict[str, Any]]:
    fixture_path = Path(__file__).parent.parent / "config" / "evaluator_human_annotations.json"
    assert fixture_path.exists(), f"Annotation fixture not found at {fixture_path}"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data: list[dict[str, Any]] = json.load(f)
        return data


def test_human_annotation_count() -> None:
    """Verify that there are at least 10 human-annotated benchmark examples."""
    annotations = load_annotation_fixture()
    assert len(annotations) >= 10, f"Expected >=10 annotations, found {len(annotations)}"


def test_evaluator_human_agreement() -> None:
    """Evaluate all benchmark items and verify high Cohen's Kappa agreement."""
    annotations = load_annotation_fixture()
    human_labels: list[str] = []
    evaluator_labels: list[str] = []

    print("\n--- Human Annotation vs Evaluator Comparison ---")
    for item in annotations:
        eval_result = evaluate_rca(
            ground_truth=item["ground_truth"],
            model_diagnosis=item["model_diagnosis"],
            classification=item["classification"],
            target_service=item["target_service"],
            incident_title=item["incident_title"],
            incident_description=item["incident_description"],
        )
        h_label = item["human_label"]
        e_label = eval_result.rca_result.value

        human_labels.append(h_label)
        evaluator_labels.append(e_label)

        match = "[MATCH]" if h_label == e_label else "[MISMATCH]"
        print(f"{match} {item['id']}: Human={h_label}, Evaluator={e_label}")

    p_o, kappa = compute_cohens_kappa(human_labels, evaluator_labels)
    print(f"\nTotal Cases Tested: {len(annotations)}")
    print(f"Observed Agreement: {p_o:.1%}")
    print(f"Cohen's Kappa (k) : {kappa:.3f}")

    # Assert near-perfect to strong agreement
    assert p_o >= 0.90, f"Observed agreement too low: {p_o:.1%}"
    assert kappa >= 0.85, f"Cohen's Kappa too low: {kappa:.3f}"


def test_retroactive_run_evaluations_match_human() -> None:
    """Specifically assert that Phase 2E-2 Runs 1 & 2 are accurately evaluated."""
    annotations = load_annotation_fixture()
    run1 = next(a for a in annotations if a["id"] == "annot_01_phase2e2_run1")
    run2 = next(a for a in annotations if a["id"] == "annot_02_phase2e2_run2")

    eval_run1 = evaluate_rca(
        ground_truth=run1["ground_truth"],
        model_diagnosis=run1["model_diagnosis"],
        classification=run1["classification"],
        target_service=run1["target_service"],
        incident_title=run1["incident_title"],
        incident_description=run1["incident_description"],
    )
    assert eval_run1.rca_result.value == "PARTIALLY_CORRECT"

    eval_run2 = evaluate_rca(
        ground_truth=run2["ground_truth"],
        model_diagnosis=run2["model_diagnosis"],
        classification=run2["classification"],
        target_service=run2["target_service"],
        incident_title=run2["incident_title"],
        incident_description=run2["incident_description"],
    )
    assert eval_run2.rca_result.value == "PARTIALLY_CORRECT"
