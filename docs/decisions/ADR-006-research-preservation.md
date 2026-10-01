# Title: Research layer preservation and compatibility
# Status: Accepted
# Date: 2026-10-01
# Source: production-readiness audit; Council A (docs/councils/council-A-architecture.md)

## Context
Phases 1-5 produced a research artifact (replay, evaluator, human-annotation benchmark, fault-injection harness, MCP experiment, results and documentation) whose value depends on remaining runnable and unchanged. The audit found defects in it that must not be carried into the product (DATA-01, AI-01..04, REL-07/08).

## Decision
* Keep `src/`, `tests/`, `scripts/`, `eval/`, `config/` and the phase documents **in place**. Do not move them to `research/`: every import, script and documented command would break for no product benefit.
* Tag the frozen state (`v1.1-research-freeze`). Only packaging, lint and reproducibility fixes are allowed in the research layer; research behaviour (including its known defects) is not changed, so published results stay reproducible.
* Archive the research run record (`eval/archive/`), pin the OTel Demo fixture (`config/otel-demo-fixture.patch`), and freeze a held-out set (`eval/heldout/`).
* The research test suite stays in CI.

## Consequences
Two RCA engines exist. The README and architecture docs state clearly that `src/` is the research engine and `backend/aegis` is the product. Research questions about the *product* are answered by running the product's benchmark (`aegis.benchmark`), optionally from research scripts.

## Alternatives rejected
Refactoring `src/` into the product (inherits SQLite, regex parsing, synchronous execution); moving research to `research/` (breakage without benefit).
