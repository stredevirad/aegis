# Phase 2C: OpenSearch LogsProvider

## Purpose
The `OpenSearchProvider` adapter satisfies the `LogsProvider` interface. It translates LLM-generated natural string queries into bounded OpenSearch queries and returns normalized `Evidence` objects containing structured telemetry.

## Architecture
Like Prometheus, the adapter isolates the backend implementation.
`ToolDispatcher` → `LogsProvider` (Interface) → `OpenSearchProvider` (Implementation) → OpenSearch HTTP REST API.

## OpenSearch Endpoint
Configured dynamically by reading `config/opentelemetry-demo.yaml`, extracting `telemetry.logs.endpoint`. When using the local OTel Demo, this connects to an ephemeral port mapped to port 9200.

## Index Pattern
Logs are retrieved from the index pattern `/otel-logs-*/_search`. 

## Document Schema
The default OTel OpenSearch export schema (as observed in Phase 2C) maps:
- **Timestamp**: `@timestamp` or `observedTimestamp` inside `_source`.
- **Log Message**: `body` inside `_source`.
- **Severity/Level**: `severity.text` inside `_source`.
- **Service Name**: `resource.service.name` inside `_source`.

## Search Semantics
The query payload sent to OpenSearch uses a `bool` query:
- A `must` block utilizing `query_string` for searching log messages via lucene syntax.
- A `filter` block utilizing `range` to constrain results tightly within the requested `@timestamp` range.

## Time Bounds
To prevent resource abuse, query windows (`end_time - start_time`) are strictly limited to `MAX_QUERY_WINDOW_HOURS` (12 hours).

## Evidence Mapping
OpenSearch hits are normalized into `Evidence` objects:
- `id`: Generates a stable deterministic hash from `_index::_id`.
- `type`: `EvidenceType.LOG`
- `source_provider`: `opensearch`
- `service_resource`: Populated from `service.name`.
- `summary`: A concise formatted string `[LEVEL] truncated_message`.
- `data`: Stores the original unstructured body, level, doc id, index, and custom `attributes`.

## Error Handling
Returns `RuntimeError` on invalid JSON, OpenSearch-specific API errors (like missing indices or parsing failures), and `httpx.HTTPStatusError`.

## Testing
- **Unit**: Tests HTTP errors, success parses, and time bound limits without an active OpenSearch backend.
- **Integration**: The test probes `http://localhost:9200` for `frontend` application logs if the OpenSearch container is accessible.

## Live Verification
The implementation is validated live by invoking `python -m scripts.verify-phase2c`.

Results:
- **Backend**: Successfully connected to `http://localhost:<ephemeral-port>`.
- **Query**: "frontend" mapped via `search_logs` against OpenSearch indices.
- **Evidence Count**: Successfully returned 100 Evidence objects (the bounded max `size` set in the provider) matching the live application traffic.
- **Evidence Structure**:
  - `id`: e.g. `ev_log_14f098541b99`
  - `type`: `EvidenceType.LOG`
  - `source_provider`: `opensearch`
  - `service_resource`: `frontend-proxy`
  - `summary`: e.g. `[UNKNOWN] [2026-08-23T15:07:59.234Z] "GET /images/products/StarsenseExplorer.jpg HTTP/1.1" 200 - via_upstream ...`

## Known Limitations
The adapter intentionally omits heavy OpenSearch DSL abstractions or client libraries, performing lightweight raw `httpx` JSON payloads to respect project boundaries. It only extracts common OTel demo fields and skips deeper nested span log correlation for now.
