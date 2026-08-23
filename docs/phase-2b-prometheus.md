# Phase 2B: Prometheus Provider

## Purpose
The `PrometheusProvider` serves as Aegis's adapter to Prometheus metrics backends. It acts as the translation layer between Aegis's generic `MetricsProvider` interface and the specific HTTP/JSON PromQL API.

## Architecture
The Prometheus adapter is strictly bounded. Aegis core domains (e.g. `InvestigationEngine`, `ToolDispatcher`, `Hypothesis`) remain entirely unaware of Prometheus. The dependency chain is:
`ToolDispatcher` → `MetricsProvider` (Interface) → `PrometheusProvider` (Implementation) → Prometheus HTTP API.

## Prometheus Endpoint
The endpoint is injected during construction. For local development with the OpenTelemetry Demo, the default endpoint is `http://localhost:9090`.

## Configuration
The `PrometheusProvider` is configured dynamically by reading `config/opentelemetry-demo.yaml` (which requires `PyYAML` to be installed and loaded in the environment). If the environment profile calls for live telemetry and sets `metrics.type: prometheus`, the integration wiring (e.g. in `demo.py --live-telemetry`) instantiates `PrometheusProvider(endpoint=...)` instead of `MockMetricsProvider`.

## Supported Operations
- **`get_status()`**: Issues a lightweight HTTP GET to `/-/healthy`. Returns `ProviderStatus` (`HEALTHY` or `UNAVAILABLE`).
- **`query_metrics()`**: Submits range queries to `/api/v1/query_range`.
- **`get_service_health()`**: (Stubbed) Currently returns `UNKNOWN` as dynamic threshold-free health inference is out of scope for Phase 2B.

## Query Semantics
The `query` string passed to `query_metrics()` is treated as a native **PromQL** query by the adapter. Aegis core does not parse, modify, or validate this query syntax; it merely passes the LLM-generated string to the adapter.

Time windows are rigorously bounded to a maximum of 12 hours. Any query exceeding this window is rejected locally to prevent massive, unoptimized telemetry pulls from overwhelming Aegis or the Prometheus server.

## Evidence Mapping
Responses from Prometheus are normalized into generic Aegis `Evidence` objects (Type: `METRIC`):
- `data`: Contains the raw values and labels extracted from the Prometheus JSON payload.
- `service_resource`: Inferred from standard labels.
- `observed_at`: Represents the time of the most recent metric sample (timezone-aware).
- `collected_at`: The exact time Aegis fetched the data (timezone-aware).
- `id`: A stable, deterministic hash derived from the query string, metric labels, and the `observed_ts`.

## Service Label Mapping
The OTel Demo convention exposes services through the `service_name` or `job` label. The provider maps `metric.get("service_name") or metric.get("job")` to the generic `Evidence.service_resource` field.

## Error Handling
The provider converts all lower-level `httpx` errors (timeouts, connection refused) and Prometheus API error payloads (`status="error"`) into standardized Python `RuntimeError`s. This ensures the engine intercepts them predictably without leaking HTTP-specific abstractions.

## Testing
- Unit tests (`tests/test_prometheus_provider.py`) heavily utilize `unittest.mock` to validate logical bounds, error handling, and Evidence mapping without requiring a live Prometheus server.
- Integration tests (`tests/test_prometheus_integration.py`) probe the actual running Prometheus instance (`http_server_request_duration_seconds_count`), bypassing execution if the backend is down.

## Live Verification
The implementation is validated live by invoking `python -m src.investigation.demo --live-telemetry --mock-llm`. This wires up the `PrometheusProvider`, queries live OTel data, and processes the evidence directly in the Investigation Engine framework without degrading the `RECORDED` state machine trajectory.
