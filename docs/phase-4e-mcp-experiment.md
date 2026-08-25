# Phase 4E — Native vs MCP Overhead Comparative Experiment

**Component**: Protocol Overhead & Performance Benchmark  
**Script**: `scripts/run_mcp_overhead_experiment.py`  
**Evaluation Scope**: Latency, serialization payload size, error rates, and provenance preservation  
**Status**: COMPLETE (Phase 4E)  

---

## 1. Experimental Design & Methodology

To determine the performance and operational tradeoffs of adopting the Model Context Protocol (MCP) in an autonomous investigation engine, we conducted a controlled comparative benchmark across three execution conditions:

1. **Condition A (Native In-Memory)**: Direct dispatch via `ToolDispatcher.dispatch()` calling provider implementations directly in the engine process.
2. **Condition B (In-Process MCP)**: Invocation through `AegisMCPServer.mcp.call_tool()` within the same Python process, measuring MCP schema validation and serialization overhead.
3. **Condition C (Out-of-Process Stdio MCP)**: Invocation via `AegisMCPClient` connecting to `python -m src.adapters.mcp` over standard I/O pipes, measuring full process and IPC protocol overhead.

### Controlled Parameters
- **Mock Backends**: Identical deterministic telemetry data across all conditions.
- **Payload & Arguments**: Exact same queries and parameters across conditions.
- **Sample Size**: $N = 5$ iterations per tool per condition.

---

## 2. Empirical Benchmark Measurements

| Tool Name | Native Dispatch Mean (ms) | In-Process MCP Mean (ms) | Stdio Subprocess MCP Mean (ms) | In-Process Overhead vs Native | Provenance Match Rate |
|---|:---:|:---:|:---:|:---:|:---:|
| `get_service_health` | 0.177 ± 0.060 ms | 5.573 ± 7.905 ms | 5,768.53 ± 1,225 ms | +5.39 ms | **100% (Passed)** |
| `get_service_dependencies` | 0.108 ± 0.041 ms | 2.570 ± 0.790 ms | 4,876.29 ± 151 ms | +2.46 ms | **100% (Passed)** |
| `query_metrics` | 0.174 ± 0.043 ms | 1.736 ± 0.167 ms | 5,066.92 ± 396 ms | +1.56 ms | **100% (Passed)** |
| `search_logs` | 0.141 ± 0.013 ms | 2.193 ± 0.788 ms | 4,803.44 ± 268 ms | +2.05 ms | **100% (Passed)** |
| `search_traces` | 0.172 ± 0.039 ms | 2.515 ± 1.228 ms | 4,631.74 ± 199 ms | +2.34 ms | **100% (Passed)** |

---

## 3. Findings & Analysis

### 3.1 Serialization & Protocol Layer Overhead (In-Process)
- **Baseline Cost**: Pure in-process MCP conversion adds approximately **1.5 ms to 5.5 ms** of latency per invocation.
- **Factors**: Pydantic schema validation, argument dictionary parsing, dynamic tool registration resolution, and JSON-RPC `TextContent` encapsulation.
- **Significance for RCA**: In an iterative investigation loop involving 4–8 tool turns, in-process MCP introduces negligible total delay (< 30 ms), making it viable for embedded tool routing.

### 3.2 Subprocess & IPC Transport Cost (Cold-Start Stdio)
- **Cold-Start Subprocess Overhead**: Cold-start stdio MCP incurs multi-second process/initialization overhead in the current implementation (~4.6 s to 5.7 s on Windows/CPython 3.13) because `AegisMCPClient.invoke_tool()` launches a fresh subprocess, imports dependencies (`mcp`, `pydantic`, `anyio`), and performs the full JSON-RPC initialization handshake for each single-shot invocation. This measurement reflects process spawn and interpreter startup, **not** steady-state MCP protocol latency.
- **Session Architecture Note**: Persistent session reuse is not currently implemented in `AegisMCPClient`. For high-frequency telemetry querying in production, external MCP integrations should maintain persistent client sessions rather than spawning short-lived on-demand processes.

### 3.3 Evidence Provenance & Fidelity
- Across all 5 tools and all experimental iterations, **100% of Evidence fields** (`id`, `type`, `source_provider`, `service_resource`, `observed_at`, `collected_at`, `summary`, `data`) were preserved without data loss or timestamp degradation.
- MCP serves as an effective, safe protocol boundary when Evidence domain models are properly reconstructed.

### 3.4 Diagnostic Quality Note
- Because MCP is strictly a tool transport and serialization protocol, diagnostic accuracy is determined entirely by the underlying telemetry providers and LLM reasoning. MCP does not intrinsically improve or degrade RCA diagnostic capability.
