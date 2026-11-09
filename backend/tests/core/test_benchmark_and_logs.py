"""Benchmark gate, evaluator semantics, and log-template robustness."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aegis.benchmark.evaluate import Expectation, Outcome, score
from aegis.benchmark.run import load_scenarios, run_scenarios, summarise
from aegis.benchmark.synth import ScenarioSpec, generate
from aegis.core import analyze
from aegis.core.logs import TEMPLATE_MAX, template_of

SCENARIOS = Path(__file__).resolve().parents[3] / "eval" / "scenarios"


@pytest.fixture(scope="module")
def results():
    specs = load_scenarios(SCENARIOS)
    assert len(specs) >= 15
    return run_scenarios(specs)


def test_synthetic_benchmark_gate(results):
    scores, _ = results
    summary = summarise(scores)
    assert summary["confident_wrong"] == 0
    failing = [
        s.case_id
        for s in scores
        if s.outcome not in (Outcome.CORRECT, Outcome.ABSTAINED_APPROPRIATELY)
    ]
    assert not failing, failing


def test_generator_is_deterministic():
    spec = load_scenarios(SCENARIOS)[1]
    a, _ = generate(spec)
    b, _ = generate(spec)
    assert a.model_dump() == b.model_dump()


def _spec(**expected) -> tuple[ScenarioSpec, object]:
    spec = next(s for s in load_scenarios(SCENARIOS) if s.id == "s06-feature-flag-failure")
    snap, req = generate(spec)
    return spec, analyze(snap, req)


def test_evaluator_flags_wrong_root_as_confident_wrong():
    _, analysis = _spec()
    s = score(
        "x",
        analysis,
        Expectation(classifications=["CONFIRMED", "PLAUSIBLE"], root_services=["payments"]),
    )
    assert s.confident_wrong and s.outcome is Outcome.INCORRECT


def test_evaluator_flags_any_root_on_a_healthy_expectation():
    _, analysis = _spec()
    s = score("x", analysis, Expectation(classifications=["NO_ANOMALY"], no_anomaly_allowed=True))
    assert s.confident_wrong


def test_evaluator_accepts_matching_expectation():
    _, analysis = _spec()
    s = score(
        "x",
        analysis,
        Expectation(
            classifications=["CONFIRMED"],
            root_services=["catalog"],
            mechanisms=["change_induced:feature_flag"],
        ),
    )
    assert s.outcome is Outcome.CORRECT and not s.confident_wrong


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=3000))
def test_template_of_never_fails_and_is_bounded(body):
    t = template_of(body)
    assert 0 < len(t) <= TEMPLATE_MAX
    assert not any(ord(c) < 32 and c not in "\t" for c in t)


def test_template_masks_variable_parts():
    a = template_of(
        "user 12345 failed login from 10.0.0.7 at 2026-01-01T10:00:00Z id=550e8400-e29b-41d4-a716-446655440000"
    )
    b = template_of(
        "user 999 failed login from 192.168.1.20 at 2026-02-03T11:22:33Z id=123e4567-e89b-12d3-a456-426614174000"
    )
    assert a == b


def test_every_evidence_item_carries_provenance():
    """Prompt §37: claim -> evidence -> telemetry source. No evidence item without a source."""
    missing = []
    for spec in load_scenarios(SCENARIOS):
        snap, req = generate(spec)
        missing += [(spec.id, e.kind.value) for e in analyze(snap, req).evidence if not e.sources]
    assert not missing, missing


def test_modality_check_reasons_read_as_plain_text():
    """Operators read check reasons verbatim: "supported by logs, metrics", not a list repr."""
    from aegis.core.result import CheckName

    reasons = set()
    for spec in load_scenarios(SCENARIOS):
        snap, req = generate(spec)
        for cand in analyze(snap, req).candidates:
            reasons |= {c.reason for c in cand.checks if c.name == CheckName.MODALITY_AGREEMENT}
    assert "supported by logs, metrics, traces" in reasons
    assert "only metrics modality supports it" in reasons
    assert not [r for r in reasons if "[" in r or "'" in r], reasons
