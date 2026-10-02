"""Pure validation of a model's adjudication against the deterministic analysis (ADR-009).

The deterministic layer is the ceiling. A model may *select* among existing candidates,
*explain* with claims that cite existing evidence, and *lower* classification or support. It
may not raise them, cite evidence that does not exist, name a service that is not a candidate,
invent a mechanism, or turn an INSUFFICIENT result into a diagnosis.

Outcomes:

* ``accepted`` — the assessment is within bounds; ``final`` carries the possibly-lowered
  classification/support, the validated claims and the model's text (sanitised).
* ``rejected`` — ``errors`` lists every violation. The caller gives the model one repair
  attempt with these errors; after that the deterministic verdict stands, marked
  "AI assessment invalid".

Rules (each has a test in ``tests/llm/test_validate.py``):

1. classification no stronger than the deterministic one (CONFIRMED > PLAUSIBLE >
   INSUFFICIENT = NO_ANOMALY); NO_ANOMALY only if deterministic is NO_ANOMALY; a model may
   move PLAUSIBLE→INSUFFICIENT but never INSUFFICIENT→PLAUSIBLE;
2. support no higher than the deterministic support;
3. ``selected_candidate`` is null or one of the analysis' candidates; choosing a candidate that
   is not a deterministic root cause caps classification at PLAUSIBLE and is reported as a
   disagreement (never CONFIRMED);
4. when classification is PLAUSIBLE/CONFIRMED a candidate must be selected;
5. ``mechanism`` is "unknown" or exactly the deterministic mechanism of the selected root cause;
6. every evidence ID cited anywhere exists in the analysis; claims without citations are
   dropped (and the assessment is rejected if no cited claim remains while asserting a cause);
7. at least one claim cites evidence the deterministic layer associated with the selected
   candidate (supporting or check evidence) — "real but irrelevant citations" are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aegis.core.result import (
    CLASSIFICATION_STRENGTH,
    SUPPORT_ORDER,
    Analysis,
    Classification,
    Support,
)
from aegis.core.sanitize import clean_text, single_line
from aegis.llm.schema import Adjudication


@dataclass
class ValidationResult:
    accepted: bool
    errors: list[str] = field(default_factory=list)
    final: dict[str, Any] | None = None


def _strength(c: str) -> int:
    return CLASSIFICATION_STRENGTH[Classification(c)]


def validate(adj: Adjudication, analysis: Analysis) -> ValidationResult:
    errors: list[str] = []
    v = analysis.verdict
    det_class, det_support = v.classification, v.support
    evidence_ids = {e.id for e in analysis.evidence}
    candidates = {c.service: c for c in analysis.candidates}
    root_causes = {rc.service: rc for rc in v.root_causes}

    # 1-2: never above the deterministic ceiling
    if _strength(adj.classification) > CLASSIFICATION_STRENGTH[det_class]:
        errors.append(
            f"classification {adj.classification} is stronger than the deterministic {det_class.value}"
        )
    if adj.classification == "NO_ANOMALY" and det_class is not Classification.NO_ANOMALY:
        errors.append("NO_ANOMALY is only allowed when the deterministic result is NO_ANOMALY")
    if det_class is Classification.NO_ANOMALY and adj.classification != "NO_ANOMALY":
        errors.append(
            "the deterministic analysis found no anomaly; the classification must stay NO_ANOMALY"
        )
    if SUPPORT_ORDER.index(Support(adj.support)) > SUPPORT_ORDER.index(det_support):
        errors.append(f"support {adj.support} is higher than the deterministic {det_support.value}")

    # 3-4: candidate membership
    selected = adj.selected_candidate
    disagreement = False
    if selected is not None and selected not in candidates:
        errors.append(f"selected_candidate {selected!r} is not one of the analysis candidates")
    asserting = adj.classification in ("CONFIRMED", "PLAUSIBLE")
    if asserting and selected is None:
        errors.append("a PLAUSIBLE or CONFIRMED assessment must select a candidate")
    if (
        selected is not None
        and selected in candidates
        and root_causes
        and selected not in root_causes
    ):
        disagreement = True
        if adj.classification == "CONFIRMED":
            errors.append(
                "choosing a candidate other than the deterministic root cause caps the result at PLAUSIBLE"
            )
    if asserting and selected is not None and selected not in root_causes and not root_causes:
        errors.append(
            "the deterministic analysis named no root cause; the assessment cannot assert one"
        )

    # 5: mechanism
    if adj.mechanism != "unknown":
        rc = root_causes.get(selected or "")
        if rc is None or adj.mechanism != rc.mechanism.value:
            errors.append(
                f"mechanism {adj.mechanism!r} must be 'unknown' or the deterministic mechanism of the selected root cause"
            )

    # 6: citations exist
    unknown = sorted(
        {eid for c in adj.claims for eid in c.evidence_ids} | set(adj.contradicting_evidence_ids)
    )
    unknown = [eid for eid in unknown if eid not in evidence_ids]
    if unknown:
        errors.append(f"unknown evidence IDs cited: {', '.join(unknown[:10])}")
    claims = [c for c in adj.claims if c.evidence_ids]
    dropped = len(adj.claims) - len(claims)
    if asserting and not claims:
        errors.append("an assessment that asserts a cause needs at least one claim citing evidence")

    # 7: relevance of citations to the selected candidate
    if asserting and selected in candidates:
        cand = candidates[selected]
        related = set(cand.supporting_evidence_ids) | {
            eid for ch in cand.checks for eid in ch.evidence_ids
        }
        if selected in root_causes:
            related |= set(root_causes[selected].supporting_evidence_ids)
        if not any(eid in related for c in claims for eid in c.evidence_ids):
            errors.append(f"no claim cites evidence the analysis associated with {selected!r}")

    if errors:
        return ValidationResult(False, errors)

    final_class = adj.classification
    if disagreement and _strength(final_class) > CLASSIFICATION_STRENGTH[Classification.PLAUSIBLE]:
        final_class = "PLAUSIBLE"
    lowered = _strength(final_class) < CLASSIFICATION_STRENGTH[det_class] or SUPPORT_ORDER.index(
        Support(adj.support)
    ) < SUPPORT_ORDER.index(det_support)
    return ValidationResult(
        True,
        [],
        {
            "selected_candidate": selected,
            "classification": final_class,
            "support": adj.support,
            "mechanism": adj.mechanism,
            "disagreement": disagreement,
            "lowered": lowered,
            "claims": [
                {
                    "type": c.type,
                    "text": single_line(c.text)[:400],
                    "evidence_ids": list(dict.fromkeys(c.evidence_ids)),
                }
                for c in claims
            ],
            "uncited_claims_dropped": dropped,
            "contradicting_evidence_ids": list(dict.fromkeys(adj.contradicting_evidence_ids)),
            "uncertainty": clean_text(adj.uncertainty)[:600],
            "missing_information": [single_line(m)[:200] for m in adj.missing_information],
            "summary": clean_text(adj.summary)[:800],
            "next_check": single_line(adj.next_check)[:300] if adj.next_check else None,
        },
    )
