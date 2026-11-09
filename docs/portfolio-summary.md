> **Research-era document (2026-08)**, kept as a historical record of the research prototype (`src/`). The current product is described in [README.md](../README.md).

# Aegis — Executive Portfolio Summary

## Project Summary
**Aegis** is an evidence-grounded incident investigation and root-cause analysis engine designed to automate production microservice triage using open telemetry standards and small local language models. It connects directly to live Prometheus, OpenSearch, and Jaeger instances, normalizes heterogeneous signals into a unified `Evidence` domain model, and executes multi-turn diagnostic investigations with strict budget enforcement. Aegis demonstrates that targeted error filtering and pre-LLM structured summarization enable a local 4B model to accurately localize failing components and avoid false negatives, while maintaining complete evidence provenance and auditable persistence.

---

## Technical Highlights
- **Hexagonal / Clean Architecture**: Strict boundary separation where domain models (`src/domain/`) have zero external adapter dependencies; supports seamless provider swapping (live, replay, mock).
- **Multi-Modal Normalization**: Seamlessly normalizes PromQL metrics, OpenSearch log hits, and Jaeger span trees into a common `Evidence` schema with ISO-8601 UTC timestamps and structured payloads.
- **Explicit Finite State Machine**: Replaced opaque multi-agent frameworks with a deterministic, auditable investigation lifecycle (`COLLECTING` $\rightarrow$ `HYPOTHESES` $\rightarrow$ `VERIFYING` $\rightarrow$ `CONCLUDING` $\rightarrow$ `RECORDED`) with hard turn, tool, and duration bounds.
- **Resilience & Graceful Degradation**: Tested across single and multi-provider outages; the engine continues investigations with available modalities without crashing.
- **Deterministic Offline Replay**: JSON snapshot infrastructure allows exact replay of telemetry bundles for offline prompt engineering and regression benchmarking.

---

## Research Highlights
- **Pre-LLM Diagnostic Summarization**: Demonstrated that in evaluated fault scenarios, structured statistical summarization (latency percentiles, error breakdowns, service activity) is essential for small LLMs; naive context truncation caused catastrophic false negatives (`NO_ANOMALY` during active outages).
- **Error-Focused Query Strategy**: Demonstrated that querying error-specific metrics (`5xx` status codes) and error logs yields a **2.3x investigation speedup** and a **59% reduction in evidence noise** in the evaluated comparison.
- **Epistemic Honesty Under Neutral Prompts**: Observed that when prompt hints are eliminated ("Service Anomaly Detected"), the model concludes `INSUFFICIENT` rather than hallucinating unsupported root causes.
- **RCA Evaluator with Leak Detection**: Built a deterministic evaluation engine that penalizes prompt echoes and requires causal language, achieving substantial agreement (**$\kappa = 0.778$**, **83.3% observed agreement**) against a 12-case reference benchmark.

---

## Measurable Results

| Metric | Measured Value | Verification Source |
|---|---|---|
| **Automated Test Suite** | **165 collected; 162 passed; 3 skipped** (offline live-integration) | `pytest tests/ -v` |
| **Static Type Safety** | **0 issues** across 54 source files | `mypy src/ tests/` (strict mode) |
| **Code Formatting / Lint** | **0 errors** | `ruff check src/ tests/` |
| **Investigation Speedup** | **2.3x faster** (137s vs 312s) | Phase 3.2 Error-focused querying |
| **Evidence Volume Reduction** | **59% reduction** (66 vs 163 items) | Phase 3.2 Telemetry filtering |
| **Evaluator Human Agreement** | **$P_o = 83.3\%$, $\kappa = 0.778$** | 12-case reference benchmark |
| **Telemetry Integration** | **3 backends verified live** | Prometheus, OpenSearch, Jaeger |

---

## Honest Limitations
1. **Single-Human Reference Annotation**: The 12-case benchmark was scored by a single human annotator, establishing reference agreement rather than multi-expert consensus.
2. **Model Mechanism Deduction Limit**: Local 4B models reliably localize affected services and symptoms, but cannot deduce underlying configuration mechanisms (such as feature flag toggles) without explicit config-plane telemetry.
3. **Single Microservice Benchmark**: Validated on the OpenTelemetry Astronomy Shop demo; enterprise deployments may introduce greater topology complexity.
4. **Recommendation-Only Remediation**: Automatic execution of operational changes is deliberately disabled to enforce human-in-the-loop safety.
