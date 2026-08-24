"""Phase 4E: Native vs MCP Overhead Comparative Benchmark Experiment.

Measures latency, transport overhead, serialization payload size, and
provenance preservation across:
- Condition A: Native ToolDispatcher execution
- Condition B: In-process MCP Server execution
- Condition C: Subprocess Stdio MCP Client/Server execution
"""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.mcp import (
    AegisMCPClient,
    create_mcp_server,
)
from src.adapters.mock_providers import (
    MockLogsProvider,
    MockMetricsProvider,
    MockTopologyProvider,
    MockTracesProvider,
)
from src.domain import ToolCall
from src.investigation.tools import ToolDispatcher


def run_benchmark(iterations: int = 10) -> dict[str, Any]:
    """Execute controlled comparative benchmark across Native, In-Process MCP, and Stdio MCP."""
    now = datetime.now(timezone.utc)
    
    # 1. Setup Native ToolDispatcher
    native_dispatcher = ToolDispatcher(
        metrics_provider=MockMetricsProvider(),
        logs_provider=MockLogsProvider(),
        topology_provider=MockTopologyProvider(),
        traces_provider=MockTracesProvider(),
        incident_start_time=now,
    )

    # 2. Setup In-Process MCP Server
    inproc_server = create_mcp_server(tool_dispatcher=native_dispatcher)

    # 3. Setup Stdio MCP Client
    stdio_client = AegisMCPClient(
        server_command=sys.executable,
        server_args=["-m", "src.adapters.mcp"],
        allowed_tools={
            "query_metrics",
            "search_logs",
            "search_traces",
            "get_service_health",
            "get_service_dependencies",
        },
    )

    test_calls = [
        ("get_service_health", {"service_name": "payment-api"}),
        ("get_service_dependencies", {"service_name": "frontend"}),
        ("query_metrics", {"query": "payment-api latency"}),
        ("search_logs", {"query": "payment-api errors"}),
        ("search_traces", {"service_name": "payment-api"}),
    ]

    results: dict[str, dict[str, Any]] = {}

    for tool_name, args in test_calls:
        tool_results: dict[str, list[float]] = {
            "native": [],
            "inproc_mcp": [],
            "stdio_mcp": [],
        }
        payload_sizes: dict[str, int] = {}
        provenance_ok = True

        # Run iterations
        for i in range(iterations):
            # A. Native execution
            t0 = time.perf_counter()
            call = ToolCall(id=f"call_{i}", name=tool_name, arguments=args)
            res_native = native_dispatcher.dispatch(call)
            t1 = time.perf_counter()
            tool_results["native"].append((t1 - t0) * 1000.0)
            if i == 0:
                payload_sizes["native"] = len(str(res_native))

            # B. In-process MCP execution
            async def _run_inproc() -> Any:
                t0_in = time.perf_counter()
                res_in = await inproc_server.mcp.call_tool(tool_name, args)
                t1_in = time.perf_counter()
                return (t1_in - t0_in) * 1000.0, res_in

            dt_in, res_inproc = asyncio.run(_run_inproc())
            tool_results["inproc_mcp"].append(dt_in)
            if i == 0:
                payload_sizes["inproc_mcp"] = len(res_inproc.content[0].text) if res_inproc.content else 0

            # C. Stdio MCP Client execution
            async def _run_stdio() -> Any:
                t0_st = time.perf_counter()
                res_st = await stdio_client.invoke_tool(tool_name, args)
                t1_st = time.perf_counter()
                return (t1_st - t0_st) * 1000.0, res_st

            dt_st, res_stdio = asyncio.run(_run_stdio())
            tool_results["stdio_mcp"].append(dt_st)
            if i == 0:
                payload_sizes["stdio_mcp"] = len(str(res_stdio.summary))
                if hasattr(res_native, "evidence") and hasattr(res_stdio, "evidence"):
                    if len(res_native.evidence) != len(res_stdio.evidence):
                        provenance_ok = False

        results[tool_name] = {
            "native_latency_ms": {
                "mean": round(statistics.mean(tool_results["native"]), 3),
                "std": round(statistics.stdev(tool_results["native"]), 3) if iterations > 1 else 0.0,
                "min": round(min(tool_results["native"]), 3),
                "max": round(max(tool_results["native"]), 3),
            },
            "inproc_mcp_latency_ms": {
                "mean": round(statistics.mean(tool_results["inproc_mcp"]), 3),
                "std": round(statistics.stdev(tool_results["inproc_mcp"]), 3) if iterations > 1 else 0.0,
                "min": round(min(tool_results["inproc_mcp"]), 3),
                "max": round(max(tool_results["inproc_mcp"]), 3),
            },
            "stdio_mcp_latency_ms": {
                "mean": round(statistics.mean(tool_results["stdio_mcp"]), 3),
                "std": round(statistics.stdev(tool_results["stdio_mcp"]), 3) if iterations > 1 else 0.0,
                "min": round(min(tool_results["stdio_mcp"]), 3),
                "max": round(max(tool_results["stdio_mcp"]), 3),
            },
            "payload_size_bytes": payload_sizes,
            "provenance_preserved": provenance_ok,
        }

    return {
        "benchmark_timestamp": datetime.now(timezone.utc).isoformat(),
        "iterations_per_tool": iterations,
        "tools": results,
    }


if __name__ == "__main__":
    print("Running Phase 4E Native vs MCP Overhead Benchmark (N=5 iterations)...")
    data = run_benchmark(iterations=5)
    print(json.dumps(data, indent=2))
