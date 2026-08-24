"""Aegis Model Context Protocol (MCP) Adapter Package.

Phase 4A: Exposes read-only Aegis investigation tools over MCP.
"""
from src.adapters.mcp.server import (
    AegisMCPServer,
    create_mcp_server,
    serialize_evidence,
    serialize_tool_result,
)

__all__ = [
    "AegisMCPServer",
    "create_mcp_server",
    "serialize_evidence",
    "serialize_tool_result",
]
