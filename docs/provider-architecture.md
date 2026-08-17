# Provider Architecture

## Philosophy
Providers act as the adapter layer between the Aegis investigation engine and external systems. Interfaces are intentionally kept minimal and narrow. Only operations required by the current milestone are defined.

## Source Adapters (Evidence)
Source adapters retrieve telemetry.
* `MetricsProvider`: Queries metrics and calculates service health (e.g., Prometheus adapter). Returns structured `ServiceHealth` models and normalized `Evidence`.
* `LogsProvider`: Queries structured or unstructured logs (e.g., Loki adapter).
* `TracesProvider`: Queries distributed traces and spans (e.g., Tempo or Jaeger adapter).

### Query Convention
`MetricsProvider.query_metrics(query, ...)` and `LogsProvider.search_logs(query, ...)` accept a backend-specific pass-through `query` string (e.g., PromQL for Prometheus, LogQL for Loki, CloudWatch metric namespace for AWS). The investigation engine's tool layer is responsible for constructing the query appropriate to the registered provider. The core domain does NOT interpret query syntax.

## Environment Adapters (Context)
Environment adapters describe the deployment context.
* `EnvironmentProvider`: Retrieves deployment metadata, versions, and configuration changes.
* `TopologyProvider`: Retrieves service dependency graphs dynamically.

## AI & Remediation Adapters
* `LLMProvider`: Abstracts the underlying language model logic. V1 uses `OllamaProvider`. Supports multi-turn conversations, tool calling, and thinking mode via generic `LLMRequest`/`LLMResponse` models.
* `RemediationProvider`: Produces remediation recommendations. V1 is recommendation-only; this provider MUST NOT execute operational changes (restarts, rollbacks, scaling, config mutations). Execution will be gated behind Human Approval in a future version.

## Capability Discovery
The system dynamically probes providers to establish available capabilities via `BaseProvider.get_status()`. Aegis degrades gracefully if a provider is unavailable (e.g., if Traces are unavailable, investigation proceeds with lower confidence).
