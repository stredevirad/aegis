"""Aegis Model Context Protocol (MCP) Adapter Package.

Phase 4A: Exposes read-only Aegis investigation tools over MCP.
"""
from src.adapters.mcp.client import AegisMCPClient
from src.adapters.mcp.otel_demo import (
    OTEL_DEMO_MUTATING_TOOLS,
    OTEL_DEMO_READ_ONLY_TOOLS,
    OTelDemoMCPAdapter,
)
from src.adapters.mcp.server import (
    AegisMCPServer,
    create_mcp_server,
    serialize_evidence,
    serialize_tool_result,
)

__all__ = [
    "AegisMCPClient",
    "AegisMCPServer",
    "OTEL_DEMO_MUTATING_TOOLS",
    "OTEL_DEMO_READ_ONLY_TOOLS",
    "OTelDemoMCPAdapter",
    "create_mcp_server",
    "serialize_evidence",
    "serialize_tool_result",
]
