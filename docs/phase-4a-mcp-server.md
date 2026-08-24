# Phase 4A — Model Context Protocol (MCP) Server Adapter

**Component**: Aegis MCP Server  
**Package**: `src/adapters/mcp/`  
**Protocol / SDK Version**: Model Context Protocol Python SDK `mcp>=2.0.0` (Protocol Spec: `2024-11-05`)  
**Status**: COMPLETE (Phase 4A)  

---

## 1. Architectural Philosophy & Domain Separation

The Model Context Protocol (MCP) server in Aegis operates strictly as an **adapter/protocol boundary**. It exposes selected, read-only investigation and telemetry querying capabilities to external MCP clients (such as Claude Desktop, IDE sidecars, or orchestration agents) without replacing or altering Aegis's internal domain abstractions.

```mermaid
flowchart LR
    subgraph External_MCP_Clients [External MCP Clients]
        ClaudeDesktop[Claude Desktop]
        IDESidecar[IDE Sidecar / Cursor / Cline]
    end

    subgraph MCP_Boundary [src/adapters/mcp/ (Adapter Layer)]
        MCPServerInstance[MCPServer (mcp 2.0.0)<br/>stdio / SSE Transport]
        Serialization[Evidence & ToolResult Serializers]
    end

    subgraph Internal_Engine [Aegis Investigation Infrastructure]
        ToolDispatcher[ToolDispatcher<br/>Budget & Time Window Enforcement]
        SQLiteRepo[(SQLiteRepository<br/>aegis.db / History)]
    end

    subgraph Telemetry_Adapters [Telemetry Providers]
        Prometheus[PrometheusProvider]
        OpenSearch[OpenSearchProvider]
        Jaeger[JaegerProvider]
    end

    External_MCP_Clients <-->|MCP JSON-RPC over stdio| MCPServerInstance
    MCPServerInstance --> Serialization
    MCPServerInstance --> ToolDispatcher
    MCPServerInstance --> SQLiteRepo
    ToolDispatcher --> Prometheus & OpenSearch & Jaeger
```

### Architectural Guarantees
1. **Strict Domain Isolation**: `src/domain/` has zero dependencies on or imports of MCP types (`CallToolResult`, `MCPServer`, etc.).
2. **No Query Duplication**: All telemetry querying logic is delegated directly to the existing `ToolDispatcher` and provider adapters (`PrometheusProvider`, `OpenSearchProvider`, `JaegerProvider`).
3. **Immutability of Evidence Provenance**: Every Evidence object returned over MCP preserves its unique `id`, `type`, `source_provider`, `service_resource`, `observed_at`, and `collected_at` timestamps.
4. **Safety & Bounds Enforcement**: All queries enforce 15-minute maximum time windows, string length bounds, result limits, and read-only execution. Remediation actions remain recommendation-only.

---

## 2. Exposed MCP Tools & Argument Schemas

The MCP server exposes 10 bounded, read-only tools:

| Tool Name | Purpose | Required Arguments | Bounds & Validation |
|---|---|---|---|
| `query_metrics` | Query metrics for a service or resource | `query: str` | `max_length=200`, bounded 15-min window |
| `search_logs` | Search logs for error/warning patterns | `query: str` | `max_length=200`, bounded 15-min window |
| `search_traces` | Query distributed traces for a service | `service_name: str` | `max_length=100`, bounded 15-min window |
| `get_trace` | Get detailed span breakdown for trace ID | `trace_id: str` | `max_length=100` |
| `get_service_health` | Inspect operational health status | `service_name: str` | `max_length=100` (`HEALTHY`, `DEGRADED`, etc.) |
| `get_service_dependencies` | Query dependency graph for a service | `service_name: str` | `max_length=100` |
| `get_evidence` | Query persisted evidence records | `evidence_id: str \| None`, `incident_id: str \| None`, `service_resource: str \| None`, `evidence_type: str \| None`, `limit: int = 50` | `limit` clamped to `[1, 100]` |
| `query_error_metrics` | Query HTTP 5xx error metrics | `service_name: str` | `max_length=100`, error-rate focus |
| `search_error_logs` | Search ERROR / WARN severity logs | `service_name: str` | `max_length=100` |
| `search_error_traces` | Search traces with error status tags | `service_name: str` | `max_length=100` |

---

## 3. Evidence Mapping & Serialization

When tools return `ToolResult`, the MCP adapter serializes the response into a structured JSON payload delivered inside an MCP `TextContent` block:

```json
{
  "status": "success",
  "evidence_count": 1,
  "summary": "Elevated latency on payment-api",
  "evidence": [
    {
      "id": "ev_metric_1",
      "type": "METRIC",
      "source_provider": "prometheus",
      "service_resource": "payment-api",
      "summary": "Elevated latency on payment-api",
      "observed_at": "2026-08-24T16:00:00.000000+00:00",
      "collected_at": "2026-08-24T16:05:00.000000+00:00",
      "data": {
        "metric_name": "http_request_duration_ms",
        "value": 1500.0,
        "unit": "ms",
        "threshold": 500.0
      }
    }
  ]
}
```

### Error Isolation
If a provider fails or is unreachable (e.g. Prometheus connection refused), the adapter catches the exception and returns a structured error response:
```json
{
  "status": "error",
  "error": "Prometheus connection refused on port 9090",
  "evidence_count": 0,
  "evidence": []
}
```
The MCP server never raises unhandled exceptions that would crash the client connection.

---

## 4. Configuration & Usage

### 4.1 Running via CLI (Standard I/O)
To start the Aegis MCP Server over `stdio` transport:

```powershell
# Using mock providers (offline / testing)
python -m src.adapters.mcp

# Using live OpenTelemetry Demo telemetry backends
python -m src.adapters.mcp --live-telemetry --db aegis.db
```

### 4.2 Claude Desktop Configuration
Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aegis": {
      "command": "python",
      "args": [
        "-m",
        "src.adapters.mcp",
        "--live-telemetry",
        "--db",
        "C:\\Users\\LENOVO\\Desktop\\Aegis\\aegis.db"
      ],
      "cwd": "C:\\Users\\LENOVO\\Desktop\\Aegis"
    }
  }
}
```

---

## 5. Verification & Testing

The Phase 4A test suite covers:
- Tool schema registration metadata and argument reflection
- Valid tool invocation across metrics, logs, traces, and dependency tools
- Persisted Evidence retrieval by ID, Incident ID, and modality filter
- Clamped limits and missing argument error handling
- Provider outage isolation and empty result resilience
- Evidence provenance serialization

```powershell
# Run MCP test suite
pytest tests/test_mcp_server.py -v
# ================= 15 passed in 2.61s =================

# Run full test suite (137 tests passing)
pytest tests/ -v

# Run type checking and linter
mypy src/ tests/
ruff check src/ tests/
```

---

## 6. Limitations

1. **Read-Only Scope**: The MCP server only exposes telemetry inspection and Evidence retrieval. It intentionally does not expose automatic remediation execution.
2. **Local Transport Focus**: The default entrypoint implements the standard `stdio` transport; HTTP/SSE transport can be used programmatically via `server.mcp.run_sse_async()`.
