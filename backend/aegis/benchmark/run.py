"""Benchmark runner: synthetic scenarios (and, via heldout.py, the frozen held-out set).

Usage (from backend/):
    python -m aegis.benchmark.run --scenarios ../eval/scenarios --out ../eval/results/benchmark.json
    python -m aegis.benchmark.run --scenarios ../eval/scenarios --gate   # exit 1 on failure

The CI gate fails when any scenario is confident-wrong or not CORRECT/ABSTAINED_APPROPRIATELY.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from aegis.benchmark.evaluate import Expectation, Outcome, Score, score
from aegis.benchmark.synth import ScenarioSpec, generate
from aegis.core import analyze
from aegis.core.stats import wilson_interval


def load_scenarios(directory: Path) -> list[ScenarioSpec]:
    specs: list[ScenarioSpec] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        specs.append(ScenarioSpec.model_validate(data))
    return specs


def run_scenarios(specs: list[ScenarioSpec]) -> tuple[list[Score], list[dict[str, Any]]]:
    scores: list[Score] = []
    details: list[dict[str, Any]] = []
    for spec in specs:
        t0 = time.perf_counter()
        snapshot, request = generate(spec)
        t1 = time.perf_counter()
        analysis = analyze(snapshot, request)
        t2 = time.perf_counter()
        exp = Expectation(**spec.expected.model_dump())
        s = score(spec.id, analysis, exp)
        scores.append(s)
        details.append(
            {
                **s.as_dict(),
                "category": spec.category,
                "title": spec.title,
                "display_label": analysis.verdict.display_label,
                "next_checks": [nc.action for nc in analysis.verdict.next_checks],
                "coverage_gaps": analysis.verdict.coverage_gaps,
                "leaders": [
                    {"service": c.service, "rule": c.rule}
                    for c in analysis.candidates
                    if c.rule is not None
                ],
                "evidence_items": len(analysis.evidence),
                "snapshot": {
                    "spans": len(snapshot.spans),
                    "logs": len(snapshot.logs),
                    "series": len(snapshot.metrics),
                },
                "seconds_generate": round(t1 - t0, 3),
                "seconds_analyze": round(t2 - t1, 3),
            }
        )
    return scores, details


def summarise(scores: list[Score]) -> dict[str, Any]:
    outcomes = Counter(s.outcome.value for s in scores)
    asserting = [s for s in scores if s.classification in ("PLAUSIBLE", "CONFIRMED")]
    correct_assertions = sum(1 for s in asserting if s.roots_ok and not s.confident_wrong)
    lo, hi = wilson_interval(correct_assertions, len(asserting))
    return {
        "cases": len(scores),
        "outcomes": dict(sorted(outcomes.items())),
        "confident_wrong": sum(1 for s in scores if s.confident_wrong),
        "asserted_root_causes": len(asserting),
        "asserted_root_cause_precision": (
            correct_assertions / len(asserting) if asserting else None
        ),
        "asserted_root_cause_precision_wilson95": [round(lo, 3), round(hi, 3)],
        "by_classification": dict(Counter(s.classification for s in scores)),
        "caveat": (
            "Synthetic scenarios authored by the pipeline's own engineering effort; a "
            "regression benchmark, not independent evidence of real-world accuracy."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Aegis RCA benchmark")
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--gate", action="store_true", help="exit 1 unless every case passes")
    args = parser.parse_args(argv)

    specs = load_scenarios(args.scenarios)
    scores, details = run_scenarios(specs)
    summary = summarise(scores)
    report = {"summary": summary, "cases": details}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    width = max(len(s.case_id) for s in scores)
    for d in details:
        sys.stdout.write(
            f"{d['case_id'].ljust(width)}  {d['outcome']:<24} {d['classification']:<12} "
            f"{d['support']:<8} roots={d['named_roots']} mech={d['mechanisms']}\n"
        )
        for n in d["notes"]:
            sys.stdout.write(f"{'':{width}}    - {n}\n")
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    if args.gate:
        ok = all(s.outcome in (Outcome.CORRECT, Outcome.ABSTAINED_APPROPRIATELY) for s in scores)
        return 0 if ok and summary["confident_wrong"] == 0 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
