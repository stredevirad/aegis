# Phase 1 Runtime Implementation

## Development Model
`qwen3.5:4b`
The 4B parameter model is now the designated development runtime. It enables iterative agentic development by producing responses faster under the current laptop constraints, keeping average turn latency practical for continuous local testing.

## Architectural Modes
To address heavy local generation latency, Aegis Phase 1 now introduces two discrete investigation modes:
- **`FAST` (Development Default)**: deterministic evidence acquisition -> LLM hypothesis generation -> bounded deterministic verification -> LLM diagnosis/recommendation.
- **`ITERATIVE` (Research Baseline)**: LLM-driven iterative tool use -> bounded evidence gathering -> hypotheses -> verification -> conclusion.

## Evidence Architecture
To preserve strict provenance and reduce context length, Aegis natively decouples authoritative evidence payloads from LLM context:
- **Authoritative Provenance**: Providers return actual `Evidence` models bundled inside a `ToolResult`. These objects are deduplicated and appended directly to `Incident.evidence` and persisted immutably in SQLite.
- **LLM Summary Views**: A compact, derived representation of the evidence (e.g., metric summaries or log counts) is separately extracted from the `ToolResult` and inserted into the LLM context. No synthetic `Evidence` models are fabricated from LLM-formatted summaries.

## 4B Live Result
**SUCCESS**: Using the `FAST` architecture mode, the 4B local development model successfully traversed the investigation pipeline and generated a complete, evidence-grounded root cause analysis.
- **Final State**: `RECORDED`
- **Duration**: 1 minute 18 seconds (78.1s)
- **LLM Turns**: 3
- **Tool Calls**: 3
- **Diagnosis**: "The payment API latency spike is caused by an internal timeout error connecting to the downstream database, resulting in request failures or extreme delays within the payment service."
- **Recommendation**: "Immediate scaling of the database read replicas and implementation of circuit breakers for downstream timeouts"

## 9B Baseline
**FAILED**: The 9B model was evaluated previously on the fully `ITERATIVE` loop and successfully initialized and communicated natively. It successfully broke out of the `COLLECTING_EVIDENCE` loop and deterministically generated two hypotheses, but it failed to complete the investigation because it exhausted the configuration budgets (~34s/turn).

## Resource Observations
- **Memory/RAM**: Machine remained stable.
- **Latency**: By bypassing the LLM round-trip constraint on individual tool executions during evidence collection, the total timeline duration dramatically dropped from over 5 minutes to 1 minute 18 seconds, well within the 210s boundary.

## Phase 1 Status
**PHASE 1 READY FOR PHASE 2 REVIEW**
The Phase 1 autonomous investigation engine is now fully functional end-to-end, locally, against a real sub-5B parameter LLM via Ollama. It autonomously produced a diagnosis and recommendation from raw generated metrics and correctly synchronized the state machine through to `RECORDED`. The SQLite persistence layer correctly logged all timeline transitions. Phase 2 (OpenTelemetry Integration) is now unblocked.
