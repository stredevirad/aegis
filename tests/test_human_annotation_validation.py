"""Validation of RCA Evaluator against Independent Human Annotations.

Validates the Phase 3.1 requirement:
The RCA evaluator must be validated against human annotation of >=10 diagnoses,
measuring Observed Agreement, Cohen's Kappa, and a Confusion Matrix.

Methodological Guardrail:
- If human labels are unpopulated (set to None), the test reports that human
  annotation is pending and skips execution instead of asserting against synthetic data.
- Once genuine human labels are provided, the test calculates exact inter-rater agreement.
"""
import json
from pathlib import Path
from typing import Any

import pytest

from src.evaluation.rca_evaluator import evaluate_rca

ALLOWED_LABELS = {"CORRECT", "PARTIALLY_CORRECT", "INCORRECT", "INSUFFICIENT_EVIDENCE"}


def compute_cohens_kappa(human_labels: list[str], evaluator_labels: list[str]) -> tuple[float, float]:
    """Compute Observed Agreement and Cohen's Kappa between human and evaluator ratings.
    
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


def build_confusion_matrix(human_labels: list[str], evaluator_labels: list[str]) -> dict[str, dict[str, int]]:
    """Build a confusion matrix mapping (Human Label -> (Evaluator Label -> Count))."""
    labels = sorted(list(ALLOWED_LABELS))
    matrix: dict[str, dict[str, int]] = {h: {e: 0 for e in labels} for h in labels}
    for h, e in zip(human_labels, evaluator_labels):
        if h in matrix and e in matrix[h]:
            matrix[h][e] += 1
    return matrix


def print_confusion_matrix(matrix: dict[str, dict[str, int]]) -> None:
    """Print formatted confusion matrix."""
    labels = sorted(list(ALLOWED_LABELS))
    col_header = "Human \\ Eval"
    header = f"{col_header:<22} | " + " | ".join(f"{lbl:<18}" for lbl in labels)
    sep = "-" * len(header)
    print("\n" + sep)
    print("CONFUSION MATRIX (Rows: Human, Columns: Evaluator)")
    print(sep)
    print(header)
    print(sep)
    for h in labels:
        row_str = f"{h:<22} | " + " | ".join(f"{matrix[h][lbl]:<18}" for lbl in labels)
        print(row_str)
    print(sep)


def load_annotation_fixture() -> list[dict[str, Any]]:
    fixture_path = Path(__file__).parent.parent / "config" / "evaluator_human_annotations.json"
    assert fixture_path.exists(), f"Annotation fixture not found at {fixture_path}"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data: list[dict[str, Any]] = json.load(f)
        return data


def test_human_annotation_fixture_structure() -> None:
    """Verify that the annotation template contains >=10 well-formed candidate cases."""
    annotations = load_annotation_fixture()
    assert len(annotations) >= 10, f"Expected >=10 candidate cases, found {len(annotations)}"
    
    for idx, item in enumerate(annotations):
        assert "id" in item and item["id"], f"Item {idx} missing id"
        assert "ground_truth" in item, f"Item {idx} missing ground_truth"
        assert "model_diagnosis" in item, f"Item {idx} missing model_diagnosis"
        assert "target_service" in item, f"Item {idx} missing target_service"
        assert "incident_title" in item, f"Item {idx} missing incident_title"
        assert "incident_description" in item, f"Item {idx} missing incident_description"
        # Validate that if a human label is provided, it is within the allowed set
        if item.get("human_label") is not None:
            assert item["human_label"] in ALLOWED_LABELS, f"Invalid human_label: {item['human_label']}"


def test_human_annotation_agreement() -> None:
    """Evaluate agreement between human annotations and automated evaluator.
    
    If annotations are not yet provided by an external human annotator,
    this test reports the pending status and skips to prevent false claims.
    """
    annotations = load_annotation_fixture()
    labeled_items = [item for item in annotations if item.get("human_label") is not None]

    if not labeled_items:
        pytest.skip(
            "Human annotation pending: config/evaluator_human_annotations.json contains "
            f"{len(annotations)} unannotated items awaiting independent human labeling. "
            "See docs/human-annotation-guide.md for instructions."
        )

    if len(labeled_items) < len(annotations):
        print(f"\nWarning: Only {len(labeled_items)}/{len(annotations)} items have human labels.")

    human_labels: list[str] = []
    evaluator_labels: list[str] = []

    print("\n--- Human Annotation vs Evaluator Comparison ---")
    for item in labeled_items:
        eval_result = evaluate_rca(
            ground_truth=item["ground_truth"],
            model_diagnosis=item["model_diagnosis"],
            classification=item["classification"],
            target_service=item["target_service"],
            incident_title=item["incident_title"],
            incident_description=item["incident_description"],
        )
        h_label = str(item["human_label"])
        e_label = eval_result.rca_result.value

        human_labels.append(h_label)
        evaluator_labels.append(e_label)

        match = "[MATCH]" if h_label == e_label else "[MISMATCH]"
        print(f"{match} {item['id']}: Human={h_label:<20} | Evaluator={e_label:<20}")

    p_o, kappa = compute_cohens_kappa(human_labels, evaluator_labels)
    matrix = build_confusion_matrix(human_labels, evaluator_labels)
    
    print_confusion_matrix(matrix)
    print(f"Total Labeled Cases: {len(labeled_items)}")
    print(f"Observed Agreement : {p_o:.1%}")
    print(f"Cohen's Kappa (k)  : {kappa:.3f}")

    # Threshold checks for completed human validation (>= 0.70 for substantive agreement)
    assert p_o >= 0.70, f"Observed agreement below threshold: {p_o:.1%}"
    assert kappa >= 0.60, f"Cohen's Kappa below threshold: {kappa:.3f}"
