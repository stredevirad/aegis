# Aegis 1.1 — Final Adversarial Research Audit: Phase 4 + Phase 5

**Audit Date**: 2026-08-25  
**Auditor**: Independent Senior Architecture & Research Methodology Review  
**Repository State**: Branch `feature/mcp-interoperability`, commit `0d5fa91`  
**Baseline**: `v1.0-research-complete` frozen  

---

## 0. Verification Baseline

| Dimension | Measured | Notes |
|:---|:---:|:---|
| **pytest** | **162 passed, 3 skipped** | Skips are live integration (Jaeger, OpenSearch, OTel MCP) — expected offline |
| **mypy** | **0 issues, 54 files** | Clean strict typing |
| **ruff** | **All checks passed** | Clean lint |
| **Working tree** | **Clean** | No uncommitted changes |

> [!IMPORTANT]
> Multiple documentation files contain **stale numeric claims** from the v1.0 freeze era. These must be corrected before final freeze. See [Section 6](#6-documentation-accuracy-discrepancies).

---

## 1. Phase 4: MCP Interoperability — Technical Audit

### 1.1 Architecture Assessment: SOUND ✅

The MCP implementation correctly preserves Aegis's hexagonal architecture:

- **`src/domain/`** has **zero imports** from `mcp`, `pydantic` MCP types, or any adapter library. Domain isolation is clean.
- MCP code lives entirely under [`src/adapters/mcp/`](file:///c:/Users/LENOVO/Desktop/Aegis/src/adapters/mcp) — pure adapter boundary.
- [`AegisMCPServer`](file:///c:/Users/LENOVO/Desktop/Aegis/src/adapters/mcp/server.py#L60-L78) wraps `ToolDispatcher` without modifying it.
- [`AegisMCPClient`](file:///c:/Users/LENOVO/Desktop/Aegis/src/adapters/mcp/client.py#L21-L46) implements proper allowlisting, 10KB payload bounds, and timeout isolation.
- [`OTelDemoMCPAdapter`](file:///c:/Users/LENOVO/Desktop/Aegis/src/adapters/mcp/otel_demo.py#L30-L91) enforces read-only policy by subtracting `OTEL_DEMO_MUTATING_TOOLS` from any allowlist.

### 1.2 Evidence Provenance Through MCP: PRESERVED ✅

[`_parse_mcp_result`](file:///c:/Users/LENOVO/Desktop/Aegis/src/adapters/mcp/client.py#L149-L205) correctly reconstructs `Evidence` domain objects from MCP `TextContent` JSON, preserving:
- `id`, `type`, `source_provider`, `service_resource`
- ISO-8601 timestamp parsing with `datetime.fromisoformat()` 
- Fallback to `datetime.now(timezone.utc)` for missing timestamps

The OTel Demo adapter additionally stamps `source_provider = "otel-demo-mcp"` on returned evidence (line 88), maintaining distinct provenance.

### 1.3 Security Boundaries: ADEQUATE ✅

- **Allowlisting**: Both client and OTel adapter check tool names against explicit allowlists before invocation.
- **Payload Limits**: 10KB argument cap ([client.py:116](file:///c:/Users/LENOVO/Desktop/Aegis/src/adapters/mcp/client.py#L116)).
- **Timeout**: Configurable with default 15s ([client.py:39](file:///c:/Users/LENOVO/Desktop/Aegis/src/adapters/mcp/client.py#L39)).
- **Exception Isolation**: All tool handlers wrapped in try/except returning error dicts instead of crashing.
- **Read-Only Enforcement**: Mutating OTel tools (`add_to_cart`, `checkout`, `empty_cart`) are explicitly blocked.

### 1.4 Hardening Tests: VERIFIED ✅

Tests cover malformed responses, oversized payloads, timeout behavior, and allowlist rejection. 162 tests pass, including all MCP-related tests.

---

## 2. Phase 4E: MCP Overhead Benchmark — Experimental Audit

### 2.1 Methodology Assessment: DEFENSIBLE WITH CAVEATS ⚠️

**What was measured correctly:**
- Three conditions (Native, In-Process MCP, Stdio MCP) using identical `MockProvider` backends.
- Same tool calls, same arguments, same mock data across all conditions.
- `time.perf_counter()` used for high-resolution timing.
- N=5 iterations with mean/std/min/max reported.

**Methodological concern — Cold-Start Stdio:**

> [!WARNING]
> The [`AegisMCPClient.invoke_tool()`](file:///c:/Users/LENOVO/Desktop/Aegis/src/adapters/mcp/client.py#L95-L147) method creates a **fresh subprocess for every single invocation** (lines 126-127). Each `invoke_tool` call spawns `python -m src.adapters.mcp`, performs JSON-RPC initialization, executes one tool call, then destroys the process.
>
> The Phase 4E benchmark therefore measures **worst-case cold-start overhead** (4.6–5.8s), not steady-state MCP performance. This is **correctly documented** in the findings (["single-shot spawning"](file:///c:/Users/LENOVO/Desktop/Aegis/docs/phase-4e-mcp-experiment.md#L45)), but the 4E document should explicitly note that the client architecture does not yet implement persistent session reuse, and the numbers represent a known upper bound.

**What the benchmark does NOT measure:**
- Warm-start / persistent-session stdio performance (the recommendation in §3.2 of the doc).
- Memory overhead of MCP serialization.
- Concurrency behavior.

**Verdict**: The benchmark is **honest about what it measures**. The cold-start characterization is useful engineering data. The documentation correctly identifies persistent sessions as the production mitigation. No fabricated or misleading results detected.

### 2.2 Provenance Verification: CORRECTLY LIMITED ⚠️

The provenance check at [lines 116-118](file:///c:/Users/LENOVO/Desktop/Aegis/scripts/run_mcp_overhead_experiment.py#L116-L118) only compares `len(evidence)` counts, not deep field equality. The doc claims "100% of Evidence fields preserved" — this is supported by the MCP test suite's deeper assertions, but the benchmark script itself only checks evidence count. The claim is defensible given the test coverage, but the benchmark script's provenance check is shallow.

---

## 3. Phase 5: Replay-Based Model Capability Study — Experimental Audit

### 3.1 Experimental Design Assessment: SOUND ✅

The replay study makes a legitimate and well-scoped contribution:

1. **Deterministic Evidence**: Both models receive the exact same [`EvidenceSnapshot`](file:///c:/Users/LENOVO/Desktop/Aegis/src/persistence/replay.py#L28-L37) JSON, reconstructed into identical `Evidence` objects.
2. **Evaluation Consistency**: Both models are scored by the same Phase 3 `evaluate_rca()` evaluator with identical ground truth.
3. **Sequential Execution**: Models run one at a time (not concurrent), eliminating resource contention as a confounder.

### 3.2 Snapshot Quality: ADEQUATE BUT MINIMAL ⚠️

> [!WARNING]
> The study uses only **2 scenarios × 2 models = 4 data points**. Each scenario contains exactly 3 evidence items (1 metric, 1 log, 1 trace).
>
> This is sufficient to demonstrate the *feasibility* of the replay-based evaluation methodology, but **insufficient to make generalizable statistical claims** about model capability. The documentation should clearly state this is a methodology demonstration, not a statistically powered study.

**Snapshot content quality**: The snapshots contain realistic, well-structured evidence items with proper ground truth metadata. The evidence items include specific quantitative data (error rates, latency values, status codes) that provide clear diagnostic signals.

### 3.3 Results Validity: CORRECT BUT INFLATED SIGNIFICANCE ⚠️

Inspecting the [actual results](file:///c:/Users/LENOVO/Desktop/Aegis/eval/results/model_comparison_results.json):

| Model | Scenario 1 | Scenario 2 | Classification |
|---|---|---|---|
| `qwen3.5:4b` | CORRECT (103.9s) | CORRECT (145.1s) | Both CORRECT |
| `llama3.2:1b` | CORRECT (58.0s) | CORRECT (33.8s) | Both CORRECT |

Both models achieved **CORRECT** on both scenarios. The documentation claims this validates that "raw parameter scale is not a hard barrier" — this is **reasonable for the observed evidence**, but should be caveated by the fact that:

1. The snapshots contain **very high signal density** (3 items, all directly relevant, all containing explicit failure keywords like "QueuePool limit," "Timeout executing SQL transaction").
2. Both models had the failure mechanism essentially spelled out in the evidence summaries.
3. The `component_localized` field shows `false` for `llama3.2:1b` on both scenarios, meaning the 1B model's diagnosis text did **not** contain the exact target service name — yet the evaluator still scored it CORRECT due to mechanism keyword matching.

**Key observation**: The claim that `qwen3.5:4b` demonstrated "superior multi-hop causal decomposition" is **qualitatively supported** by reading the actual diagnosis texts (Qwen produces much more detailed causal chains with `cascading failure` and `triggered by` language), but this is a **qualitative observation from 2 samples**, not a statistically validated finding.

### 3.4 Evaluator Behavior in Phase 5: CONSISTENT ✅

The `evaluate_rca()` call in [`run_model_replay_comparison.py`](file:///c:/Users/LENOVO/Desktop/Aegis/scripts/run_model_replay_comparison.py#L97-L106) correctly passes:
- `ground_truth` from snapshot metadata
- `target_service` from `ground_truth_component` 
- `available_evidence` from reconstructed evidence list
- `incident_title` and `incident_description` from the reconstructed incident

Leak ratios are low (0–6.6%), confirming the evaluator does not penalize the replay diagnoses for prompt echoing.

---

## 4. Assessment of Research Claims

### 4.1 Supported Claims (Genuinely Demonstrated) ✅

| Claim | Evidence |
|:---|:---|
| MCP preserves Evidence provenance and domain isolation | Code inspection + 162 passing tests |
| In-process MCP adds ~1.5–5.5ms serialization overhead | Benchmark data with controlled conditions |
| Cold-start stdio MCP carries ~5s subprocess startup cost | Benchmark data, correctly attributed to process spawn |
| Read-only enforcement blocks mutating OTel Demo tools | Explicit allowlist subtraction + test coverage |
| Replay infrastructure enables deterministic offline evaluation | Snapshot → Evidence reconstruction → LLM invocation → evaluation pipeline verified |
| Both 4B and 1B models can identify failure mechanisms from high-density summarized evidence | 4 data points with actual LLM outputs stored |

### 4.2 Overclaimed or Insufficiently Caveated ⚠️

| Claim | Issue |
|:---|:---|
| "High Telemetry Signal Density **Empowers** Compact Models" | Demonstrated on 2 scenarios with 3 evidence items each. "Empowers" overstates the strength of evidence. Should be "preliminary observation from 2 controlled scenarios." |
| "Parameter Scale Enhances Multi-Hop Causal Decomposition" | Qualitative observation from 2 samples. Not a statistical finding. |
| "Replay-Based Determinism" enables evaluation "in < 3 minutes" | Accurate for the 2-snapshot set, but latency is model-dependent (qwen took 103-145s per scenario). |
| Portfolio summary claims "122 / 122 passing" tests | **Stale**: current suite has 165 collected, 162 passed. See Section 6. |

### 4.3 Unsupported Claims (None Detected) ✅

I found **no fabricated results** or **manufactured human annotations**. The Phase 5 results file contains genuine LLM outputs with realistic reasoning chains and appropriate variation between models.

---

## 5. Correctness Issues

### 5.1 No Critical Correctness Bugs Found ✅

- All 162 tests pass.
- Type checking is clean across 54 files.
- The evaluator logic in [`rca_evaluator.py`](file:///c:/Users/LENOVO/Desktop/Aegis/src/evaluation/rca_evaluator.py) is internally consistent.
- Evidence serialization/deserialization round-trips correctly.

### 5.2 Minor Observations (Non-Blocking)

1. **`asyncio.run()` inside loop** ([benchmark:100,112](file:///c:/Users/LENOVO/Desktop/Aegis/scripts/run_mcp_overhead_experiment.py#L100-L112)): Creates a new event loop per iteration. Not a bug, but adds measurable overhead to in-process MCP measurements. The benchmark still serves its purpose since overhead is compared relative to native.

2. **Subprocess per invocation** ([client.py:126](file:///c:/Users/LENOVO/Desktop/Aegis/src/adapters/mcp/client.py#L126)): As noted, the client creates a new process for every tool call. This is architecturally honest (no session reuse implemented yet), and correctly documented as a known limitation.

---

## 6. Documentation Accuracy Discrepancies

> [!CAUTION]
> Several documents contain stale numeric claims from the v1.0 era that no longer match the current repository state. These must be corrected before final freeze.

| Document | Stale Claim | Actual Value | Fix Required |
|:---|:---|:---|:---|
| [README.md:3](file:///c:/Users/LENOVO/Desktop/Aegis/README.md#L3) | Badge: "tests-122%20passed" | 165 collected, 162 passed, 3 skipped | Update badge |
| [README.md:23](file:///c:/Users/LENOVO/Desktop/Aegis/README.md#L23) | "122 automated unit/integration tests" | 165 tests (162 pass + 3 skip) | Update count |
| [README.md:179](file:///c:/Users/LENOVO/Desktop/Aegis/README.md#L179) | "Run all 122 unit and integration tests" | 165 tests | Update count |
| [portfolio-summary.md:29](file:///c:/Users/LENOVO/Desktop/Aegis/docs/portfolio-summary.md#L29) | "122 / 122 passing" | 162/165 (3 skipped = live integration) | Update count |
| [portfolio-summary.md:31](file:///c:/Users/LENOVO/Desktop/Aegis/docs/portfolio-summary.md#L31) | "0 issues across 43 source files" | 0 issues across **54** source files | Update file count |
| [final-phase-1-3-research-audit.md:15](file:///c:/Users/LENOVO/Desktop/Aegis/docs/final-phase-1-3-research-audit.md#L15) | "122 / 122 passed... 43 source files" | 162/165 passed; 54 source files | Update counts |
| [final-results.md:4](file:///c:/Users/LENOVO/Desktop/Aegis/docs/final-results.md#L4) | "Phases 1, 2, and 3 Complete" | Phases 1–5 complete | Update status |
| [README.md:4](file:///c:/Users/LENOVO/Desktop/Aegis/README.md#L4) | "mypy-strict%20clean" badge | Correct, but file count should be 54 not 43 | Update badge text if file count mentioned |

---

## 7. Final Freeze Recommendation

### Can Phase 4 + Phase 5 Be Frozen?

**YES**, with the following **minimum corrections**:

### Required Before Freeze (Blocking)

1. **Update all stale test/file counts** (Section 6 table) across README.md, portfolio-summary.md, final-phase-1-3-research-audit.md, and final-results.md to reflect the current 165/162/54 numbers.

2. **Add sample-size caveat to Phase 5 documentation** ([phase-5-model-comparison.md](file:///c:/Users/LENOVO/Desktop/Aegis/docs/phase-5-model-comparison.md)): Add a "Limitations" section explicitly stating:
   - N=4 data points (2 scenarios × 2 models) demonstrate methodology feasibility, not statistical generalizability.
   - Evidence snapshots contain high signal density (3 items each, all directly diagnostic).

3. **Soften Phase 5 language**: Change "Empowers Compact Models" to "Preliminary observation that compact models can succeed on high-density diagnostic evidence" or similar.

### Optional Improvements (Non-Blocking)

- Add a `docs/phase-4-5-audit.md` documenting this audit for provenance.
- Note in Phase 4E that persistent session reuse is not implemented in the current client.

### Verdict

> The Phase 4 MCP implementation is architecturally sound, properly bounded, and honestly characterized. The Phase 5 replay study produces genuine, reproducible results with real LLM outputs. No fabricated data, no inflated claims beyond the caveats noted above. The documentation discrepancies are stale counts from pre-Phase-4 freezes, not deliberate misrepresentation.
>
> **Recommendation: Correct the stale documentation counts, add Phase 5 sample-size caveats, then freeze as the final Aegis 1.1 research artifact.**
