"""Structured evaluator for the product RCA pipeline (Council B, evaluation methodology).

Scores each field separately instead of keyword-matching prose:

* classification within the acceptable set;
* named root services within the expected set (none named is acceptable only when the
  classification abstains);
* mechanism class of every named root cause;
* services that must appear among candidates or next checks;
* structural citation validity (every cited evidence ID exists in the analysis);
* **confident-wrong**: a PLAUSIBLE/CONFIRMED verdict naming a wrong or forbidden root
  service, NO_ANOMALY while a fault is active, or any root cause on a healthy system.
  This is the headline metric and must be zero.

The research evaluator (src/evaluation/rca_evaluator.py) is kept unchanged for research
comparability; this evaluator does not replace it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from aegis.core.result import Analysis, Classification


class Outcome(StrEnum):
    CORRECT = "CORRECT"  # all expectations met
    ABSTAINED_APPROPRIATELY = "ABSTAINED_APPROPRIATELY"  # INSUFFICIENT where that is acceptable
    PARTIALLY_CORRECT = "PARTIALLY_CORRECT"  # not wrong, but an expectation was missed
    INCORRECT = "INCORRECT"  # confident-wrong or forbidden output


@dataclass(frozen=True)
class Expectation:
    classifications: list[str]
    root_services: list[str] = field(default_factory=list)
    mechanisms: list[str] = field(default_factory=list)
    forbidden_root_services: list[str] = field(default_factory=list)
    must_mention: list[str] = field(default_factory=list)
    no_anomaly_allowed: bool = False


@dataclass
class Score:
    case_id: str
    outcome: Outcome
    classification: str
    support: str
    named_roots: list[str]
    mechanisms: list[str]
    classification_ok: bool
    roots_ok: bool
    mechanism_ok: bool | None
    mentions_ok: bool
    citations_valid: bool
    confident_wrong: bool
    notes: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "outcome": self.outcome.value,
            "classification": self.classification,
            "support": self.support,
            "named_roots": self.named_roots,
            "mechanisms": self.mechanisms,
            "classification_ok": self.classification_ok,
            "roots_ok": self.roots_ok,
            "mechanism_ok": self.mechanism_ok,
            "mentions_ok": self.mentions_ok,
            "citations_valid": self.citations_valid,
            "confident_wrong": self.confident_wrong,
            "notes": self.notes,
        }


def _cited(analysis: Analysis) -> set[str]:
    ids: set[str] = set()
    for c in analysis.candidates:
        ids |= set(c.supporting_evidence_ids) | set(c.contradicting_evidence_ids)
        for ch in c.checks:
            ids |= set(ch.evidence_ids)
    for rc in analysis.verdict.root_causes:
        ids |= set(rc.supporting_evidence_ids) | set(rc.contradicting_evidence_ids)
    return ids


def score(case_id: str, analysis: Analysis, exp: Expectation) -> Score:
    v = analysis.verdict
    notes: list[str] = []
    cls = v.classification
    named = [rc.service for rc in v.root_causes]
    mechs = [rc.mechanism.value for rc in v.root_causes]
    asserting = cls in (Classification.PLAUSIBLE, Classification.CONFIRMED)

    classification_ok = cls.value in exp.classifications
    if not classification_ok:
        notes.append(f"classification {cls.value} not in {exp.classifications}")

    if asserting and exp.root_services:
        roots_ok = bool(named) and set(named) <= set(exp.root_services)
    elif asserting:
        roots_ok = False  # asserting a root cause where none exists
    else:
        roots_ok = True
    if not roots_ok:
        notes.append(f"named roots {named} not within expected {exp.root_services}")

    mechanism_ok: bool | None = None
    if asserting and exp.mechanisms and named:
        mechanism_ok = all(m in exp.mechanisms for m in mechs)
        if not mechanism_ok:
            notes.append(f"mechanisms {mechs} not within {exp.mechanisms}")

    mentioned = {c.service for c in analysis.candidates}
    for nc in v.next_checks:
        mentioned |= set(nc.services)
    mention_text = " ".join(nc.action for nc in v.next_checks)
    missing = [m for m in exp.must_mention if m not in mentioned and m not in mention_text]
    mentions_ok = not missing
    if missing:
        notes.append(f"not mentioned in candidates or next checks: {missing}")

    citations_valid = _cited(analysis) <= {e.id for e in analysis.evidence}
    if not citations_valid:
        notes.append("cited evidence missing from the analysis")

    forbidden = sorted(set(named) & set(exp.forbidden_root_services))
    confident_wrong = (
        (asserting and not roots_ok)
        or bool(forbidden)
        or (cls is Classification.NO_ANOMALY and not exp.no_anomaly_allowed)
        or not citations_valid
    )
    if forbidden:
        notes.append(f"forbidden root cause named: {forbidden}")

    if confident_wrong:
        outcome = Outcome.INCORRECT
    elif classification_ok and roots_ok and mentions_ok and mechanism_ok in (True, None):
        abstained = cls is Classification.INSUFFICIENT
        outcome = Outcome.ABSTAINED_APPROPRIATELY if abstained else Outcome.CORRECT
    else:
        outcome = Outcome.PARTIALLY_CORRECT
    return Score(
        case_id=case_id,
        outcome=outcome,
        classification=cls.value,
        support=v.support.value,
        named_roots=named,
        mechanisms=mechs,
        classification_ok=classification_ok,
        roots_ok=roots_ok,
        mechanism_ok=mechanism_ok,
        mentions_ok=mentions_ok,
        citations_valid=citations_valid,
        confident_wrong=confident_wrong,
        notes=notes,
    )
