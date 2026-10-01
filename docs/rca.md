# Root-Cause Analysis in the Aegis Product

The product RCA engine is `aegis.core.analyze(snapshot, request) -> Analysis` in `backend/aegis/core/`. It is a pure function (no I/O, no clock, no randomness; enforced by `tests/core/test_purity.py`). Design decisions: [ADR-008](decisions/ADR-008-pure-rca-core.md), [ADR-009](decisions/ADR-009-llm-authority.md), [Council B](councils/council-B-rca.md). The research engine in `src/` is unchanged and separate.

## 1. Inputs

* **`TelemetrySnapshot`** (schema v1, `core/model.py`): metric series (`request_rate`, `error_ratio`, `latency_p95_ms`, `saturation`, each point optionally carrying the event count it summarises), log records (severity, body, instance, trace/span IDs, HTTP status), spans (parent links, kind, **status**, peer service), change events (deploy, config, feature flag, rollout, with scope and instances), configured topology edges, and a per-modality **collection outcome** (`ok`, `partial`, `failed`, `not_configured`). Every item carries provenance (provider, canonical query, collection time). All fields are size-capped; oversized input is a validation error.
* **`AnalysisRequest`**: the incident window, one to four baseline windows in order of preference (each must end before the incident starts), focus services (from the alert), and tolerances.

## 2. Stages

1. **Signals.** Per (service, signal): metric series where available; otherwise derived from the service's entry spans (error ratio only when span status is known for ≥80% of spans; sampled span counts are never used as request rates).
2. **Baseline validity.** The first baseline window with enough points is used; a window is trimmed at any change on the service inside it, and rejected if too little remains.
3. **Anomalies.** Error ratio: count-weighted incident ratio vs baseline median + max(1 pp, 4 × MAD-sigma with floor 0.2 pp), at least 2% and ≥20 requests; without a baseline, an absolute 5%. Latency p95: ≥2× baseline and ≥ baseline + max(50 ms, 4 × spread). Request rate: ×2 or ÷2 vs baseline (metrics only). Saturation: peak ≥ 90% when the baseline p95 is below 80%. **Onset** is the first interval exceeding the threshold, confirmed by 2 of the 3 intervals starting there — background errors before the fault are not an onset.
4. **Trace error origins.** In each failing trace, the deepest failing span whose children are all known healthy. A failing span with a child of **unknown** status makes the trace undetermined (never assumed healthy). A failing client span without collected children points at its peer (e.g. an uninstrumented database). An origin counts as anomalous when its per-trace rate is ≥3× the baseline rate.
5. **Latency attribution.** For slow traces (root duration above 2× the baseline p95), the service with the most exclusive time; client-span self time is attributed to the peer.
6. **Topology.** Configured edges ∪ caller→callee edges from parent links and client peers; per-edge latency (detects slow uninstrumented dependencies from their callers) and per-edge call-pattern diff normalised per traced request (new / vanished / increased / decreased edges surface masked faults).
7. **Log signatures.** Bodies are reduced to templates (numbers, IDs, UUIDs, IPs, emails, quoted strings, timestamps masked) and **counted, never interpreted**. A signature is "new" or "increased" relative to the baseline; a new error signature counts as independent log evidence only if it comes from at least two source instances.
8. **Change correlation.** Changes within 30 minutes before the incident window are evidence; scope matching compares the change's service and instances with the failing ones.
9. **Coverage.** Every collection that failed, was partial, truncated or not configured, unknown span status and missing parent links are reported as coverage gaps.

## 3. Ordered decision list

The first rule that selects a leader wins, evaluated per connected component of the anomalous services. When topology is unknown for the focus service, "not connected" is treated as *unknown*, not *unrelated*.

| Rule | Sentence |
|---|---|
| R1 | A service in which at least half of the failing traces end (deepest failing span) leads. |
| R1b | Services that are each the origin of a substantial share (≥25%) of failing traces and do not depend on each other lead together (independent faults). |
| R1L | A service that accounts for most of the time spent in at least half of the slow traces leads. |
| R2 | An anomalous service with a recorded change shortly before its onset and no anomalous service downstream of it leads. |
| R3 | The anomalous service that every other anomalous service depends on leads. |
| R4 | A dependency whose call pattern changed during the incident and that lies downstream of every anomalous service leads (possible masked failure). |
| R5 | The anomalous service whose onset precedes every other anomalous service by more than the tolerance band leads. |
| R6 | If exactly one service is anomalous, it leads. |

## 4. Checks

Each check is `pass`, `fail`, `not_evaluable` (the data needed is missing) or `not_applicable` (the check has no meaning here). Only `pass` ever counts.

| Check | Pass when | Fail when |
|---|---|---|
| baseline_valid | every anomaly of the candidate was measured against a valid baseline | — (otherwise not evaluable) |
| temporal_precedence | the candidate's onset precedes every anomalous dependent by more than the tolerance band (60 s) | a dependent became anomalous earlier; a tie is `not_evaluable` with qualifier `tie` |
| propagation | errors: ≥60% of relevant failing traces end in it; latency/saturation: it dominates ≥60% of slow traces (≥3 traces required either way) | ≤20% |
| isolation | all of its dependencies are evaluated and healthy (or it calls nothing) | a dependency is also anomalous |
| downstream_evaluated | every dependency has telemetry with known health | a dependency has no such telemetry |
| change_scope | a change on it preceded its onset within 30 min and its instances (if known) overlap the failing ones | the change touched other instances |
| modality_agreement | ≥2 independent modalities support it | another modality shows it healthy |

## 5. Verdict rules (ADR-009, amended)

* **CONFIRMED** — modality agreement, propagation, valid baseline, isolation **or** change scope, no failed check, no contradicting evidence, all dependencies evaluated, and temporal precedence passed (or not applicable, or a tie offset per deviation D2).
* **PLAUSIBLE** — at least one substantive check passed, none failed, no contradicting evidence, all dependencies evaluated. Independent leaders are reported together.
* **INSUFFICIENT** — anything else with anomalies; always names the next check.
* **NO_ANOMALY** — no anomaly, error origin, new error signature or changed call pattern, metrics/logs/traces all collected, at least one valid baseline. Displayed as *"Nothing found in what was checked"* with the unchecked list.
* **Support level** (ordinal, never a probability): CONFIRMED → `strong`; PLAUSIBLE with ≥2 substantive passes → `moderate`, else `weak`; otherwise `none`.
* **Mechanism** (deterministic lookup): change scope passed → `change_induced:<kind>`; saturation anomaly → `saturation`; uninstrumented dependency → `dependency_failure` / `dependency_latency`; error family → `error_throwing`; latency only → `latency_degradation`; rate surge → `traffic_surge`; R4 → `behaviour_change`; else `unknown`. A change mechanism is never claimed without change evidence.

### Deviations from the Council B verdict (with reasons)

| ID | Council B said | Implemented | Reason |
|---|---|---|---|
| D1 | A missing change feed forces INSUFFICIENT | A missing change feed is a coverage gap that **caps the result at PLAUSIBLE** and forbids `change_induced` | Forcing INSUFFICIENT would make every installation without a change feed useless while telling the operator nothing new. |
| D2 | Temporal precedence must pass for CONFIRMED; a tie is not evaluable | A tie is not evaluable, but is **offset only when trace propagation passes AND a scope-matched change precedes the onset** | In synchronous call chains a callee's failure reaches its callers within one scrape interval, so a tie is the *expected* case. Under the literal rule CONFIRMED is structurally unreachable for the most common fault class (observed on the first benchmark run). The offset needs *both* clock-independent trace evidence and a change, so the Contrarian's "coincidental deploy" path (change alone) still cannot reach CONFIRMED. |
| D3 | — | Added `downstream_evaluated`: a candidate cannot be named root cause while a dependency on its path is unevaluated | Found while writing held-out expectations: without it the symptom (frontend) would be blamed when dependencies are invisible. |
| D4 | Tri-state checks | Four states: `not_applicable` is distinct from `not_evaluable` | "No dependents to precede" and "no change to scope" are not missing data; both still never count as a pass. |

## 6. Evaluation

### Synthetic benchmark (`eval/scenarios/`, regression gate)

15 synthetic scenarios with ground truth from the injected fault: healthy baseline, HTTP 5xx, database latency (uninstrumented), external dependency outage, deployment regression, feature-flag failure, the same flag failure with the change feed withheld (label-leak control), insufficient evidence, red herring, prompt injection in logs, partial telemetry outage, masked fallback, CPU saturation, two simultaneous faults, intermittent errors. Run: `python -m aegis.benchmark.run --scenarios ../eval/scenarios --gate` (from `backend/`; also a pytest test).

Result on 2026-10-02 (`eval/results/benchmark-synthetic.json`): **15/15 pass — 12 correct, 3 appropriate abstentions, 0 confident-wrong.** This is **not** an accuracy estimate: the scenarios were authored by the same effort that built the pipeline, and they were used during development — they found three pipeline defects (background failing traces deciding the propagation check; unknown topology treated as "unrelated"; per-interval quantiles blind to intermittent faults) and one scenario authoring error, all fixed. They are a regression gate.

### Held-out set (`eval/heldout/`, 8 real OpenTelemetry Demo captures)

Frozen, with expected answers hashed (SHA-256 `95c73713…8400` of the committed file) before any pipeline code existed. The captures have no span status, no parent links, no change feed and no baseline window, and some lost evidence to the research engine's ID collisions; the honest best output for the fault cases is INSUFFICIENT naming product-catalog as the next thing to check.

| Run | Condition | Confident-wrong | Appropriate abstentions | Partial (expected mention missing) |
|---|---|---|---|---|
| 1 (`heldout-run1.json`) | clean: before any change motivated by the held-out data | **0** | 3 | 5 |
| 2 (`heldout-run2-posthoc.json`) | post-hoc: after the count-weighted error ratio (motivated by a new synthetic scenario, s15) and frequency-ranked next checks | **0** | 6 | 2 |

Run 2 is reported as post-hoc because the two changes were prompted by inspecting run 1; the clean measurement is run 1. In the two remaining partial cases the frontend's aggregate error ratio stays below the 5% absolute threshold used when no baseline exists; lowering that threshold to fit these cases would be tuning on held-out data and was not done.

**What the held-out set can and cannot show.** It shows the engine does not produce confident wrong answers on real, degraded telemetry and that it abstains while pointing at the right next check. It cannot test localisation with confidence (span status, topology, change events and baselines are absent). A recapture of the OpenTelemetry Demo with the production collectors is required for that (roadmap M10).

## 7. Known limitations

* Thresholds are engineering defaults, not calibrated; no calibration claim is made before ~50 independently labelled cases.
* In synchronous systems, CONFIRMED is reachable mainly for change-induced faults (D2); internal failures without a change are reported as PLAUSIBLE.
* Masked faults with no anomaly beyond a changed call pattern are reported as INSUFFICIENT with the changed edge as the next check.
* Forged telemetry (e.g. fake error spikes) can mislead the deterministic layer; the guarantee is "never more confident than the data", not resistance to forged data.
* Uninstrumented dependencies can reach PLAUSIBLE at most unless a second modality observes them.
