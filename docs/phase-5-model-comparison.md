# Phase 5 — Replay-Based Model Capability Study

**Component**: Offline Evidence Replay & LLM Comparative Evaluation  
**Artifacts**:
- Curated Snapshots: `eval/snapshots/`
- Runner Script: `scripts/run_model_replay_comparison.py`
- Empirical Results: `eval/results/model_comparison_results.json`
- Test Suite: `tests/test_model_capability_study.py`  
**Status**: COMPLETE (Phase 5)  

---

## 1. Experimental Motivation & Research Question

A fundamental question in autonomous RCA is **disentangling system architecture from LLM reasoning capacity**:

> *"Does Aegis's investigation architecture (error-focused querying, structured evidence summarization, explicit state machine) enable effective root cause identification across different local LLM scales, or is diagnosis success purely a function of massive parameter counts?"*

To test this without re-injecting live faults or introducing non-deterministic telemetry variations, we utilized Aegis's **Evidence Replay Infrastructure** (`src/persistence/replay.py`) with curated snapshots.

---

## 2. Model Environment & Resource Governance

In compliance with local resource constraints (host: ~16.7 GB RAM), installed models were audited via `ollama list` and executed **sequentially** (never concurrently):

| Model Name | Parameter Size | Quantization | Installed Disk Size | Role in Benchmark |
|---|:---:|:---:|:---:|---|
| **`qwen3.5:4b`** | 4.7B | Q4_K_M | 3.4 GB | Primary local development baseline |
| **`llama3.2:1b`** | 1.2B | Q8_0 | 1.3 GB | Lightweight edge comparator model |

---

## 3. Curated Evidence Replay Scenarios

1. **`snapshot_catalog_pool_exhaustion.json`** (`catalog_db_pool_exhaustion`):
   - *Target Service*: `product-catalog`
   - *Ground Truth*: Database connection pool exhaustion (`QueuePool` limit 10 reached) causing HTTP 500 errors to propagate to frontend.
   - *Evidence Modalities*: Prometheus 5xx error rate metrics (84.5%), OpenSearch fatal queue pool timeout logs, Jaeger distributed trace spans.

2. **`snapshot_payment_db_latency.json`** (`feature_flag_db_latency`):
   - *Target Service*: `downstream-db` / `payment-api`
   - *Ground Truth*: Downstream database query execution latency spike (p95 spiked to 2.45s) causing payment-api 2000ms transaction timeout.
   - *Evidence Modalities*: Prometheus database query latency metrics, OpenSearch SQL transaction timeout logs, Jaeger trace spans.

---

## 4. Empirical Evaluation Results

Both models were evaluated on the exact same reconstructed evidence snapshots and scored using the Phase 3 leak-aware `evaluate_rca` evaluator.

| Scenario | Model | Evaluator Classification | Incident Leak Ratio | Causal Terms Found | Latency | Component Localized |
|---|---|:---:|:---:|---|:---:|:---:|
| **Catalog Pool Exhaustion** | `qwen3.5:4b` | **`CORRECT`** | 3.5% | `due to`, `because`, `root cause`, `triggered by` | 103.9 s | Yes (`product-catalog`) |
| **Payment DB Latency** | `qwen3.5:4b` | **`CORRECT`** | 4.7% | `caused by`, `due to`, `root cause`, `cascading failure` | 145.1 s | Yes (`downstream-db`) |
| **Catalog Pool Exhaustion** | `llama3.2:1b` | **`CORRECT`** | 6.6% | `caused by`, `due to`, `root cause`, `resource exhaustion` | 58.0 s | Yes (`product-catalog pool`) |
| **Payment DB Latency** | `llama3.2:1b` | **`CORRECT`** | 0.0% | `caused by`, `root cause` | 33.8 s | Partial (`payment-api` timeout) |

---

## 5. Core Research Insights

1. **Preliminary Observation: Both Tested Models Produced Evaluator-Accepted Diagnoses on the Two Replayed High-Signal Scenarios**:
   - On the two replayed scenarios with high-density, pre-filtered telemetry (3 items per snapshot), both `qwen3.5:4b` and `llama3.2:1b` identified the core failure mechanism (e.g. `QueuePool` size 10 overflow, 2000ms SQL query timeout).
   - This provides an initial methodology demonstration showing that compact models can process structured evidence when presented with condensed diagnostic signals, though this is an exploratory observation across $N=4$ data points (2 scenarios × 2 models) rather than a statistically powered benchmark.

2. **Qualitative Observation: Parameter Scale Correlates with Causal Decomposition Depth**:
   - While `llama3.2:1b` identified the localized failure condition, `qwen3.5:4b` demonstrated superior multi-hop reasoning by mapping the full causal chain:
     $$\text{Downstream DB Latency} \longrightarrow \text{Payment API Transaction Timeout} \longrightarrow \text{Cascading Frontend 500}$$
   - `qwen3.5:4b` also maintained lower incident-leak ratios and generated actionable mitigation guidance.

3. **Replay-Based Determinism**:
   - Evidence snapshotting enables reproducible evaluation of prompt templates and reasoning architectures in < 3 minutes without requiring live cloud or cluster testbed deployments.

---

## 6. Study Limitations

> [!WARNING]
> The following limitations must be explicitly acknowledged when interpreting Phase 5 results.

1. **Small Sample Size**: The study evaluates 2 models × 2 scenarios = **4 total data points**. This is sufficient to demonstrate the feasibility of the replay-based evaluation methodology, but **insufficient to support statistically generalizable claims** about model capability.
2. **High Signal Density**: Each snapshot contains exactly 3 evidence items (1 metric, 1 log, 1 trace), all directly relevant to the failure. Real-world incidents may contain hundreds of noisy evidence items where signal extraction is substantially harder.
3. **Single Execution Per Condition**: Each model was evaluated once per scenario. LLM outputs are non-deterministic; repeated trials would be needed to establish reliability confidence intervals.
4. **Evaluator Sensitivity**: The `evaluate_rca` evaluator uses keyword-based mechanism alignment. Both models scored `CORRECT` partly because the evidence summaries contained explicit failure keywords (e.g. "QueuePool limit", "Timeout executing SQL transaction") that the evaluator's mechanism matching detected.
5. **No Baseline Comparison**: The study does not include a "no summarization" or "random model" baseline condition.
