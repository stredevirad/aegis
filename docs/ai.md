# Aegis — AI Adjudication

The deterministic engine (`docs/rca.md`) produces the answer. The language model is optional, off by default, and may only **select, explain and lower** within the deterministic bounds (ADR-009). The product is fully functional with AI disabled or failing.

## Pipeline

1. The worker persists and streams the deterministic verdict (`diagnosis` event).
2. If the organization enabled AI, the adjudicator builds a prompt, takes a slot from the global LLM pool, calls the configured provider once, validates the output, and gives the model **one repair attempt** with the validation errors.
3. The validated assessment (or the reason there is none) is stored as `verdict.ai`; the investigation completes. The deterministic columns (`classification`, `support`, root causes) are never changed by the model.

`verdict.ai.status`: `disabled` · `accepted` · `invalid` (rejected twice) · `unavailable` (provider down, circuit open, no capacity, misconfigured) · `refused` (cloud without opt-in, model not allowlisted) · `budget_exhausted` · `cancelled`.

## What the model may and may not do

Enforced by `aegis.llm.validate` (pure; `tests/llm/test_validate.py`):

| May | May not |
|---|---|
| select one of the analysis' candidates, or none | name a service that is not a candidate |
| explain with typed claims (`observation` / `inference`), each citing evidence IDs | cite evidence IDs that do not exist; claims without citations are dropped |
| lower classification or support | raise classification or support above the deterministic values; turn INSUFFICIENT into a diagnosis; call NO_ANOMALY what the engine found anomalous |
| state uncertainty, missing information, a suggested next check | state a mechanism other than the deterministic one (or `unknown`) |
| choose a non-leading candidate (shown as a disagreement, capped at PLAUSIBLE) | rely only on evidence unrelated to the candidate it selected |

The output schema has no reasoning field; model "thinking" is never requested (`think: false`) and is discarded if returned. Structured output: Ollama `format` (JSON schema), OpenAI-compatible strict `json_schema`, Anthropic forced tool use.

## Prompt and data protection

Instructions travel only in the system role. The analysis projection is JSON inside a block delimited by a per-call random marker and labelled untrusted; JSON encoding prevents forged lines or markers. All text is redacted again before prompting; evidence the engine cites comes first; the prompt is sized to the provider's context window (`context_window`, default 8192 tokens; Ollama's default 2048 silently truncated prompts in a live test and is never used).

## Policy (per organization)

`ai_enabled` (default off) · `ai_integration_id` (one of the org's integrations on an `llm` connector) · `ai_model` (else the connector's `default_model`; must be in the operator's `allowed_models` if set) · `ai_allow_cloud` (required for connectors with `locality: cloud`) · `ai_daily_token_budget` (rolling 24 h) · `ai_max_output_tokens`. **There is no fallback to another provider**: if the configured provider fails, the deterministic verdict stands.

Operator connector options for `llm`: `api` (`ollama` | `openai` | `anthropic`), `locality` (`local` | `cloud`), `default_model`, `allowed_models`, `context_window`.

## Records

Every call writes an `llm_calls` row: provider connector, API, model, locality, attempt (initial/repair), latency, token counts, status, error category, prompt SHA-256 and size (prompts themselves are not stored), redaction counts, and the policy that allowed it. Exposed at `GET /api/v1/investigations/{id}/llm-calls`. Audit events: `ai.model_selected`, `ai.output_accepted`, `ai.output_rejected`.

## Concurrency and failure

A global slot pool (`llm_slots`, default 1 slot for one CPU-bound local model) bounds concurrent model calls across workers; leases expire if a worker dies. A per-connector circuit breaker stops calling a provider after 3 consecutive failures for 60 s. Provider failures are not "repaired" (repair is for invalid output only).

## Observed behaviour (live, CPU, 2026-10-02)

See `docs/production-readiness.md`. With `llama3.2:1b` the structured output always parsed, but the content violated the bounds (it escalated a PLAUSIBLE case to CONFIRMED on the prompt-injection scenario, invented candidates, and cited a provider name as an evidence ID); the validator rejected it and the deterministic verdict stood. No claim is made about model accuracy from these runs.
