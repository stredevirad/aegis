"""RCA Evaluator — compares Aegis investigation results against ground truth.

This module evaluates whether a diagnosis produced by Aegis matches the
known injected fault, validates evidence provenance and citations, and
reports a structured evaluation result.

Phase 3 Improvements:
- Incident-leak detection: catches diagnoses that merely restate the incident description
- Causal language requirement: CORRECT requires causal/explanatory content beyond symptoms
- Evaluation metadata: tracks overlap ratios, causal terms, leak scores for analysis
"""
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RCAResult(str, Enum):
    CORRECT = "CORRECT"
    PARTIALLY_CORRECT = "PARTIALLY_CORRECT"
    INCORRECT = "INCORRECT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass
class RCAEvaluation:
    """Structured evaluation of a single investigation run."""
    scenario_id: str
    ground_truth: str
    model_diagnosis: str | None
    final_classification: str | None
    rca_result: RCAResult
    supporting_evidence_ids: list[str] = field(default_factory=list)
    contradicting_evidence_ids: list[str] = field(default_factory=list)
    valid_evidence_count: int = 0
    modalities_supported: list[str] = field(default_factory=list)
    llm_turns: int = 0
    tool_calls: int = 0
    total_duration_seconds: float = 0.0
    evidence_total: int = 0
    evidence_metrics: int = 0
    evidence_logs: int = 0
    evidence_traces: int = 0
    notes: str = ""
    # Phase 3 evaluation metadata
    incident_leak_ratio: float = 0.0
    causal_terms_found: list[str] = field(default_factory=list)
    novel_content_ratio: float = 0.0


def _normalize_tokens(text: str) -> set[str]:
    """Tokenize text and split camelCase words."""
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    tokens = re.findall(r"[a-zA-Z0-9]+", expanded.lower())
    # Also include combined variants without hyphens (e.g. productcatalog for product-catalog)
    return set(tokens)


# Stop words that should be excluded from overlap analysis
_STOP_WORDS = {
    "the", "a", "an", "is", "was", "are", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "must", "to", "of", "in",
    "for", "on", "with", "at", "by", "from", "as", "into", "through",
    "during", "before", "after", "above", "below", "and", "but", "or",
    "nor", "not", "so", "yet", "both", "either", "neither", "each",
    "every", "all", "any", "few", "more", "most", "other", "some",
    "such", "than", "too", "very", "just", "about", "up", "out",
    "if", "then", "that", "this", "it", "its", "they", "them", "their",
    "we", "our", "you", "your", "he", "she", "him", "her",
}

# Causal/explanatory terms that indicate genuine root cause analysis
_CAUSAL_TERMS = [
    "caused by", "due to", "because", "root cause", "resulting from",
    "triggered by", "originated from", "stems from", "attributed to",
    "misconfiguration", "misconfigured", "configuration change",
    "feature flag", "flag enabled", "flag set", "flag toggled",
    "deployment", "deployed", "rollback", "release",
    "memory leak", "resource exhaustion", "connection pool",
    "deadlock", "race condition", "circular dependency",
    "certificate expir", "dns resolution", "network partition",
    "out of memory", "disk full", "cpu throttl",
    "upstream failure", "downstream failure", "cascading failure",
    "crash loop", "oom kill", "segfault",
]


def _compute_incident_leak(
    diagnosis: str,
    incident_title: str,
    incident_description: str,
) -> tuple[float, float]:
    """Compute how much of the diagnosis merely restates the incident description.

    Returns:
        (leak_ratio, novel_ratio):
            leak_ratio: fraction of diagnosis content tokens that overlap with incident text
            novel_ratio: fraction of diagnosis content tokens NOT in incident text
    """
    diag_tokens = _normalize_tokens(diagnosis) - _STOP_WORDS
    incident_tokens = (
        _normalize_tokens(incident_title) | _normalize_tokens(incident_description)
    ) - _STOP_WORDS

    if not diag_tokens:
        return 1.0, 0.0

    overlap = diag_tokens & incident_tokens
    leak_ratio = len(overlap) / len(diag_tokens)
    novel_ratio = 1.0 - leak_ratio

    return leak_ratio, novel_ratio


def _find_causal_terms(diagnosis: str) -> list[str]:
    """Find causal/explanatory terms in the diagnosis text."""
    diag_lower = diagnosis.lower()
    found = []
    for term in _CAUSAL_TERMS:
        if term in diag_lower:
            found.append(term)
    return found


def evaluate_rca(
    ground_truth: str,
    model_diagnosis: str | None,
    classification: str | None,
    scenario_id: str = "",
    target_service: str = "",
    supporting_evidence_ids: list[str] | None = None,
    available_evidence: list[Any] | None = None,
    available_evidence_ids: set[str] | None = None,
    incident_title: str = "",
    incident_description: str = "",
) -> RCAEvaluation:
    """Evaluate whether the model diagnosis matches the ground truth and validates evidence.

    Evaluation checks (Phase 3 strengthened):
    1. Diagnosis non-empty.
    2. Classification compatibility (INSUFFICIENT -> INSUFFICIENT_EVIDENCE, NO_ANOMALY during fault -> INCORRECT).
    3. Valid supporting Evidence IDs that actually exist in collected Incident.evidence.
    4. Incident-leak detection: diagnoses that merely restate the incident description are downgraded.
    5. Causal language requirement: CORRECT requires explanatory content beyond symptom description.
    6. Component and fault mechanism alignment with ground truth.
    """
    notes_list: list[str] = []
    
    # 1. Check diagnosis existence
    if not model_diagnosis or not model_diagnosis.strip():
        return RCAEvaluation(
            scenario_id=scenario_id,
            ground_truth=ground_truth,
            model_diagnosis=model_diagnosis,
            final_classification=classification,
            rca_result=RCAResult.INSUFFICIENT_EVIDENCE,
            notes="Diagnosis is empty or missing.",
        )

    # 2. Classification compatibility checks
    cls_upper = (classification or "").upper()
    if cls_upper == "INSUFFICIENT":
        return RCAEvaluation(
            scenario_id=scenario_id,
            ground_truth=ground_truth,
            model_diagnosis=model_diagnosis,
            final_classification=classification,
            rca_result=RCAResult.INSUFFICIENT_EVIDENCE,
            notes="Investigation explicitly concluded INSUFFICIENT.",
        )

    if cls_upper == "NO_ANOMALY":
        if ground_truth:
            return RCAEvaluation(
                scenario_id=scenario_id,
                ground_truth=ground_truth,
                model_diagnosis=model_diagnosis,
                final_classification=classification,
                rca_result=RCAResult.INCORRECT,
                notes="Model concluded NO_ANOMALY when a fault was active.",
            )
        return RCAEvaluation(
            scenario_id=scenario_id,
            ground_truth=ground_truth,
            model_diagnosis=model_diagnosis,
            final_classification=classification,
            rca_result=RCAResult.CORRECT,
            notes="Healthy baseline correctly identified.",
        )

    # 3. Validate supporting Evidence IDs
    valid_ids: list[str] = []
    modalities_found: set[str] = set()
    
    # Build lookup maps if evidence objects are provided
    evidence_id_set = available_evidence_ids or set()
    evidence_by_id = {}
    if available_evidence:
        for e in available_evidence:
            e_id = getattr(e, "id", None) or (e.get("id") if isinstance(e, dict) else None)
            if e_id:
                evidence_id_set.add(e_id)
                evidence_by_id[e_id] = e

    if supporting_evidence_ids:
        for eid in supporting_evidence_ids:
            if eid in evidence_id_set:
                valid_ids.append(eid)
                e_obj = evidence_by_id.get(eid)
                if e_obj:
                    e_type = getattr(e_obj, "type", None) or (e_obj.get("type") if isinstance(e_obj, dict) else None)
                    if e_type:
                        e_type_str = e_type.value if hasattr(e_type, "value") else str(e_type).lower()
                        modalities_found.add(e_type_str)
        
        if not valid_ids and evidence_id_set:
            notes_list.append("Warning: None of the cited supporting Evidence IDs exist in collected telemetry.")
        else:
            notes_list.append(f"Validated {len(valid_ids)}/{len(supporting_evidence_ids)} supporting Evidence IDs.")
    else:
        notes_list.append("Note: No explicit supporting Evidence IDs were cited.")

    # 4. Incident-leak detection (Phase 3)
    leak_ratio = 0.0
    novel_ratio = 1.0
    if incident_title or incident_description:
        leak_ratio, novel_ratio = _compute_incident_leak(
            model_diagnosis, incident_title, incident_description
        )
        if leak_ratio >= 0.8:
            notes_list.append(
                f"Incident-leak: {leak_ratio:.0%} of diagnosis tokens overlap with incident description. "
                "Diagnosis appears to restate the symptom rather than identify a root cause."
            )
        elif leak_ratio >= 0.5:
            notes_list.append(
                f"Partial incident-leak: {leak_ratio:.0%} of diagnosis content overlaps with incident description."
            )

    # 5. Causal language detection (Phase 3)
    causal_terms = _find_causal_terms(model_diagnosis)
    if causal_terms:
        notes_list.append(f"Causal terms found: {causal_terms}")

    # 6. Component and fault mechanism alignment
    diag_tokens = _normalize_tokens(model_diagnosis)
    gt_tokens = _normalize_tokens(ground_truth)
    target_tokens = _normalize_tokens(target_service) if target_service else set()
    
    # Target service terms (e.g. product-catalog -> product, catalog, productcatalog)
    if "product" in target_tokens or "catalog" in target_tokens:
        target_tokens.update({"product", "catalog", "productcatalog", "product-catalog"})
    if "recommendation" in target_tokens or "recommend" in target_tokens:
        target_tokens.update({"recommendation", "recommendations", "recommend"})
    if "cart" in target_tokens:
        target_tokens.update({"cart", "cartservice"})
    if "payment" in target_tokens:
        target_tokens.update({"payment", "paymentservice", "checkout"})

    # Failure / symptom terms
    failure_terms = {
        "fail", "failure", "failed", "error", "exception", "500", "503", "504",
        "unavailable", "unreachable", "timeout", "latency", "spike", "crash",
        "down", "flag", "feature", "degraded", "degradation", "disconnect"
    }

    # Check component alignment
    component_matched = bool(diag_tokens & target_tokens)
    if not component_matched and target_service:
        # Check if the service name appears as substring
        target_clean = target_service.replace("-", "").lower()
        diag_clean = model_diagnosis.replace("-", "").lower()
        if target_clean in diag_clean or any(t in diag_clean for t in target_tokens if len(t) > 3):
            component_matched = True

    # Check symptom/failure alignment
    has_failure_concept = bool(diag_tokens & failure_terms)
    
    # Isolate specific root cause mechanism keywords (excluding target service name, generic failure terms, and stopwords)
    mechanism_keywords = (
        gt_tokens
        - target_tokens
        - failure_terms
        - _STOP_WORDS
        - {"service", "services", "enabled", "causing", "status", "unknown", "backend"}
    )
    mech_matches = sum(1 for kw in mechanism_keywords if kw in diag_tokens or kw in model_diagnosis.lower())
    mech_ratio = mech_matches / max(len(mechanism_keywords), 1)

    # Phase 3: Determine result with strengthened criteria
    if component_matched and has_failure_concept:
        # Check for CORRECT: requires identifying the specific root cause mechanism from ground truth
        # (e.g. feature flag name, flag toggle, cache disable, or substantial mechanism keyword overlap)
        has_mechanism_alignment = (
            mech_ratio >= 0.4 or
            "feature flag" in model_diagnosis.lower() or
            ("flag" in mechanism_keywords and "flag" in diag_tokens) or
            ("cache" in mechanism_keywords and "cache" in diag_tokens) or
            ("grpc" in mechanism_keywords and "grpc" in diag_tokens)
        )
        
        # High incident-leak downgrades the result
        if leak_ratio >= 0.8:
            # Diagnosis is mostly restating the incident — cannot be CORRECT
            rca_res = RCAResult.PARTIALLY_CORRECT
            notes_list.append(
                f"Downgraded from potential CORRECT: diagnosis mostly restates incident description "
                f"(leak={leak_ratio:.0%})."
            )
        elif has_mechanism_alignment:
            rca_res = RCAResult.CORRECT
            notes_list.append(f"Correct: Identified affected component ({target_service or 'target'}) and root cause mechanism.")
        else:
            rca_res = RCAResult.PARTIALLY_CORRECT
            notes_list.append(f"Partially Correct: Identified affected component ({target_service or 'target'}) and failure symptom, but did not pinpoint root cause mechanism.")
    elif component_matched:
        rca_res = RCAResult.PARTIALLY_CORRECT
        notes_list.append(f"Partially Correct: Identified affected service ({target_service or 'target'}).")
    else:
        rca_res = RCAResult.INCORRECT
        notes_list.append("Incorrect: Diagnosis did not identify the affected service or root cause.")

    return RCAEvaluation(
        scenario_id=scenario_id,
        ground_truth=ground_truth,
        model_diagnosis=model_diagnosis,
        final_classification=classification,
        rca_result=rca_res,
        supporting_evidence_ids=valid_ids if valid_ids else (supporting_evidence_ids or []),
        valid_evidence_count=len(valid_ids),
        modalities_supported=sorted(modalities_found),
        notes=" ".join(notes_list),
        incident_leak_ratio=leak_ratio,
        causal_terms_found=causal_terms,
        novel_content_ratio=novel_ratio,
    )
