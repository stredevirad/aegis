# Phase 1 Runtime Implementation

## Phase 1 Status
**PHASE 1 NOT READY — LIVE QWEN E2E STILL FAILS**

The Phase 1 investigation engine has been heavily corrected and explicitly validated across all internal constraints, but the real-world live inference failed to complete deterministically within reasonable bounds on local hardware.

## Live Qwen Investigation
**FAILED**: The investigation engine successfully initialized and communicated with `qwen3.5:9b` locally over 6 turns. Thanks to the new orchestration constraints, it successfully broke out of the `COLLECTING_EVIDENCE` loop and deterministically generated two hypotheses. However, it failed to complete the investigation (record a diagnosis and recommendation) because it exhausted the configuration budgets (`MAXIMUM_LLM_TURNS=6` and `MAXIMUM_INVESTIGATION_DURATION_SECONDS=180` hit simultaneously after 3 minutes and 24 seconds).

## Tool Calling
**PASS**: The `ToolDispatcher` successfully serialized schemas (`required` and Pydantic max lengths natively mapped) and executed multiple consecutive multi-turn round trips. The engine successfully preserved `tool_name` alongside the `tool_call_id` and cleanly formatted the Ollama tool payload.

## Thinking Mode
**PASS**: `OLLAMA_THINK` now maps natively to the `payload["think"]` parameter on the provider side. `<think>` tags organically returned in the raw string are explicitly extracted into the transient `LLMMessage.thinking` field, entirely segregating them from the timeline evidence.

## SQLite
**PASS**: End-to-end integration is natively supported. The engine deterministically saves `incidents`, `evidence`, `events`, `hypotheses` (with bounds `0.0 <= x <= 1.0` or `None`), and `recommendations` without crashing. 

## Tests
- **pytest**: PASS (including granular token and schema tests)
- **mypy**: PASS
- **ruff**: PASS

## Runtime Measurements
- **Average Turn Latency**: ~60-80 seconds per turn for a 9B model on the local host.
- **Tokens**: Verified token logic parsing `prompt_eval_count` and `eval_count`.
- **Observed Constraints Hit**: Total duration 204.7s, Tool Calls: 4, LLM Turns: 6. The configuring bounds (`MAXIMUM_LLM_TURNS=6`) and (`MAXIMUM_INVESTIGATION_DURATION_SECONDS=180`) were jointly exhausted, resulting in a `FAILED` state before diagnosis could be formed.

## Remaining Limitations
1. **Model Adherence Loop**: Qwen 3.5 9B locally struggles to halt tool execution and advance the state machine organically, necessitating either more aggressive systemic prompts, fine-tuning, or a larger model for autonomous Phase 1 progression.
2. **Hardware Constraints**: A complete E2E diagnosis realistically requires >5 minutes on CPU/Consumer GPU hardware, clashing with standard synchronous timeouts.
- **Connection**: Successful. The `OllamaProvider` properly performed a health check against `/api/tags` and verified the model existed.
- **Latency/Resource Notes**: A single turn generation took >60 seconds on the local hardware, triggering the default `httpx` timeout. This proved that the error handling successfully caught the timeout and gracefully transitioned the investigation to a `FAILED` state rather than crashing. 
- **Action Taken**: The internal HTTP timeout was increased to `180.0s` to accommodate the heavy 9B parameter model on local hardware without a dedicated GPU. RAM usage remained stable throughout the single generation attempt.

## Known Limitations
- The extracted diagnosis and recommendations use very basic string matching in Phase 1 (`"DIAGNOSIS:"` and `"RECOMMENDATION:"`). More robust structured parsing (e.g. JSON extraction or native tool calls for the final result) would improve reliability.
- Time window calculation relies on passing `start_time` into the dispatcher, assuming a static runtime execution window.

## Next Phase
Phase 2: BLOCKED

The system is NOT READY for OpenTelemetry Demo Integration until the complete live Qwen investigation succeeds.
