# Phase 1 Runtime Implementation

## Development Model
`qwen3.5:4b`
The 4B parameter model is now the designated development runtime. It enables iterative agentic development by producing responses faster under the current laptop constraints, keeping average turn latency practical for continuous local testing.

## Research Model
`qwen3.5:9b`
The 9B parameter model is intentionally retained as the research baseline. It previously served as the initial local baseline but was too slow for rapid iterative development. This distinction will later allow controlled research comparing the 4B vs 9B models under identical investigation scenarios.

## 4B Live Result
**FAILED**: The investigation engine was successfully updated to structurally parse the recommendation directly alongside the diagnosis, and the defaults were successfully updated to a 7-turn, 210-second budget. However, the model suddenly experienced heavy latency during the live run (averaging ~52 seconds per turn) and subsequently breached the `MAXIMUM_INVESTIGATION_DURATION_SECONDS=210` wall-clock time limit midway through the verification stage.
- **Final State**: `FAILED` (Exact failure point: `Exceeded MAXIMUM_INVESTIGATION_DURATION (210s)`)
- **Duration**: 5 minutes 16 seconds (wall-clock time before the background runner completely halted).
- **LLM Turns**: 6
- **Tool Calls**: 6
- **Average Turn Latency**: ~52 seconds per turn

## 9B Baseline
**FAILED**: The 9B model was evaluated previously and successfully initialized and communicated natively. It successfully broke out of the `COLLECTING_EVIDENCE` loop and deterministically generated two hypotheses, but it also failed to complete the investigation (record a diagnosis and recommendation) because it exhausted the configuration budgets.
- **Final State**: `FAILED`
- **Duration**: 3 minutes 24 seconds (204.7s)
- **LLM Turns**: 6 (hit `MAXIMUM_LLM_TURNS=6` budget limit)
- **Tool Calls**: 4
- **Average Turn Latency**: ~34 seconds per turn

## Resource Observations
- **Memory/RAM**: Machine remained stable.
- **Latency**: The 4B model experienced a severe degradation in inference speed on this iteration, bloating to an average of ~52s per turn compared to the previous ~24s average, causing the time limits to forcibly terminate the investigation.

## Phase 1 Status
**PHASE 1 NOT READY**
The runtime constraints effectively protected the orchestration loops, but the 4B model still failed to reach a conclusion organically because it hit the explicit 210-second duration budget before it could finalize its verification stage. The Phase 2 integration remains explicitly blocked.
