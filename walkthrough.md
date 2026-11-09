> **Research-era document (2026-08)**, kept as a historical record of the research prototype (`src/`). The current product is described in [README.md](README.md).

# Aegis — End-to-End Revalidation & Project Closure Walkthrough

This document records the comprehensive verification, experimental results, and finalized architecture of **Aegis (Phases 1, 2, and 3: Domain Core, Real Telemetry Integration, Fault Injection, Evaluation Rigor, and Reproducibility)**.

---

## 1. Project Phase Completion Matrix

| Subphase | Scope & Primary Artifacts | Status | Evidence & Verification |
| :--- | :--- | :---: | :--- |
| **Phase 1** | Pure domain models, provider interfaces, FSM engine, tool dispatcher, SQLite repository (`src/domain/`, `src/investigation/`, `src/persistence/`) | **COMPLETE** | `test_domain_models.py` (20/20), `test_engine.py` (7/7), `test_sqlite_roundtrip.py` (3/3), Mock E2E demo verified. |
| **Phase 2A** | Live OpenTelemetry Demo stack (`docker-compose.yml`, Prometheus `:9090`, OpenSearch `:56695`, Jaeger `:56020`) | **COMPLETE** | Live multi-service Astronomy Shop environment verified healthy with synthetic load. |
| **Phase 2B** | Prometheus Metrics Provider (`src/adapters/prometheus.py`) | **COMPLETE** | `test_prometheus_provider.py` (7/7), `test_prometheus_integration.py` (1/1). |
| **Phase 2C** | OpenSearch Logs Provider (`src/adapters/opensearch.py`) | **COMPLETE** | `test_opensearch_provider.py` (8/8), `test_opensearch_integration.py` (1/1). |
| **Phase 2D** | Jaeger Traces Provider (`src/adapters/jaeger.py`) | **COMPLETE** | `test_jaeger_provider.py` (6/6), `test_jaeger_integration.py` (1/1). |
| **Phase 2E-1**| Healthy Baseline Investigation (`demo.py --live-telemetry`) | **COMPLETE** | 155+ evidence items collected, 0 error logs/spans, `NO_ANOMALY` / `INSUFFICIENT` classification. |
| **Phase 2E-2**| Controlled Fault Injection & RCA (`run-fault-investigation.py`, `product-catalog-failure.yaml`) | **COMPLETE** | Baseline-vs-fault differential verified (+11 error spans), Run 1 & 2 localized `product-catalog` / 500 errors, automatic recovery verified. |
| **Phase 2F** | Provider Degradation & System Hardening | **COMPLETE** | `test_provider_degradation.py` (12/12), `test_partial_telemetry.py` (5/5), `test_llm_failure.py` (2/2), `test_evidence_provenance.py` (3/3). |
| **Phase 3.1** | Leak-Aware RCA Evaluator Overhaul (`src/evaluation/rca_evaluator.py`) | **COMPLETE** | 21 unit tests (`test_rca_evaluator.py`), 12-case human reference validation ($P_o = 83.3\%$, $\kappa = 0.778$). |
| **Phase 3.2** | Error-Focused Evidence Querying (`src/investigation/tools.py`) | **COMPLETE** | 2.3x investigation speedup (137s vs 312s), 59% evidence noise reduction (`test_error_focused_tools.py`). |
| **Phase 3.3** | Pre-LLM Evidence Summarizer (`src/investigation/evidence_summarizer.py`) | **COMPLETE** | `test_evidence_summarizer.py` (10/10), demonstrated that summarizer prevents catastrophic false negatives (`NO_ANOMALY`) in evaluated fault scenarios. |
| **Phase 3.4** | Neutral Incident Description Experiment (`product-catalog-failure-neutral.yaml`) | **COMPLETE** | Model honestly concluded `INSUFFICIENT` with 0% prompt leak when hints were removed. |
| **Phase 3.5** | Multi-Fault & Masked Fallback Experiment (`recommendationCacheFailure`) | **COMPLETE** | Established single-hop fast mode boundary under application fallbacks (0 error telemetry delta). |
| **Phase 3.6** | Deterministic Replay Infrastructure (`src/persistence/replay.py`) | **COMPLETE** | `test_evidence_replay.py` (3/3), snapshot serialization and `ReplayProvider`. |

---

## 2. Experimental Findings Summary

### Key Measured Metrics

1. **Query Strategy Comparison (Phase 3.2)**:
   - **Generic Strategy**: 312.5s duration, 3 turns, 163 evidence items.
   - **Error-Focused Strategy**: **137.5s duration (2.3x speedup)**, 2 turns, **66 evidence items (59% reduction)**.
2. **Context Summarization Impact (Phase 3.3)**:
   - **Summarizer ON**: Model detects active outage (`CONFIRMED` / `PLAUSIBLE`).
   - **Summarizer OFF (Raw Bounded)**: Naive 2-item truncation hides failure spans, causing **False Negative (`NO_ANOMALY`)**.
3. **Prompt Leak & Epistemic Uncertainty (Phase 3.4)**:
   - **Neutral Symptom Description**: 0% prompt leak, model honestly concludes `INSUFFICIENT` (`INSUFFICIENT_EVIDENCE`).
4. **Single-Human Reference Benchmark Validation (Phase 3.1)**:
   - **Observed Agreement ($P_o$)**: **83.3%** (10/12 matches).
   - **Cohen's Kappa ($\kappa$)**: **0.778** (Substantial agreement).

---

## 3. Automated Verification & Quality Assurance

```text
pytest tests/ -v
================= 162 passed, 3 skipped in ~66s ==================

mypy src/ tests/
Success: no issues found in 54 source files

ruff check src/ tests/
All checks passed!
```

- **Zero pytest warnings** (custom `integration` mark registered).
- **Strict type checking** across all 54 source and test files.
- **Mock demo execution** verified clean round-trip persistence in SQLite.
- **All fault flags recovered** and healthy baseline maintained.

---

## 4. Closure Status

Phases 1, 2, and 3 are **GENUINELY COMPLETE**. The repository is packaged as a clean, reproducible, and methodologically honest research prototype and portfolio artifact.
