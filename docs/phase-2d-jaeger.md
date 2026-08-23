# Phase 2D: Jaeger TracesProvider

## Purpose
The `JaegerProvider` adapter implements the `TracesProvider` interface. It translates ToolDispatcher trace requests into Jaeger API queries and returns normalized trace `Evidence` objects to give the investigation engine visibility into application spans and transaction latency.

## Jaeger Endpoint
Configured dynamically by reading `config/opentelemetry-demo.yaml`, extracting `telemetry.traces.endpoint`. When using the local OTel Demo, this connects to the ephemeral port mapping corresponding to Jaeger's Web UI/API port (typically `16686`).

## API Operations Used
- `GET <endpoint>/jaeger/ui/api/services`: Used by `get_status()` as a fast, lightweight health check.
- `GET <endpoint>/jaeger/ui/api/traces`: Used by `get_related_traces()` to search for traces bounding by time and service.
- `GET <endpoint>/jaeger/ui/api/traces/{trace_id}`: Used by `get_trace()` to fetch the structural data for a specific trace.

## Trace Normalization & Evidence Mapping
Jaeger hits are normalized into single Trace-level `Evidence` objects to avoid overloading the LLM context window with hundreds of span objects per trace.
- `id`: Prefixed format `ev_trace_{trace_id}`.
- `type`: `EvidenceType.TRACE`.
- `source_provider`: `"jaeger"`.
- `service_resource`: Populated from the root span's resolved `serviceName` from the Jaeger `processes` map.
- `summary`: A concise LLM-friendly string, e.g., `Trace: HTTP GET /dispatch (8 spans, 21ms)`.
- `data`: Stores the original `trace_id`, `total_spans`, and a reduced `spans` array capping at 50 spans containing just `span_id`, `operation`, `service`, and `duration_ms`.

## Time Bounds
Query windows (`end_time - start_time`) are strictly validated up to a `MAX_QUERY_WINDOW_HOURS` of 12 hours. Microsecond Unix timestamps are correctly serialized to the Jaeger query payload.

## Error Handling
Returns `RuntimeError` on invalid JSON, connection timeouts, or non-200 HTTP statuses. It accurately distinguishes between a backend failure and a successful 200 OK query that simply returned 0 traces.

## Testing
- **Unit**: Mock tests `tests/test_jaeger_provider.py` cover boundary logic, error propagation, HTTP mocking, and correct `_normalize_trace` mappings.
- **Integration**: The test `tests/test_jaeger_integration.py` runs live against `frontend` traces if the container is reachable, validating trace payload structures dynamically.

## Live Verification
The script `python -m scripts.verify-phase2d` validates the pipeline locally by querying the real Jaeger proxy and dumping a sample `ToolResult`.

## Known Limitations
The adapter intentionally omits pulling full Jaeger Python clients or massive span log data payloads, preserving the lightweight bounded approach established in metrics and logs. Parent-child structural nesting inside `data` is flattened to just structural properties to minimize token space while providing enough ID keys for the LLM to understand relationships.
