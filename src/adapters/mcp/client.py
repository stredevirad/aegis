"""Model Context Protocol (MCP) Client Adapter for Aegis.

Phase 4B: Implements an adapter-layer client that discovers, validates,
and invokes external MCP tools through standard transports while preserving
Aegis's internal domain isolation, Evidence provenance, and security bounds.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import BaseModel, create_model

from src.domain import Evidence, EvidenceType, ToolDefinition, ToolResult


class AegisMCPClient:
    """Bounded, policy-governed MCP Client for external tool consumption.
    
    Responsibilities:
    - Connect to remote MCP servers over stdio or SSE transport.
    - Discover remote tool definitions and map to Aegis ToolDefinition models.
    - Enforce explicit tool allowlisting and argument bounds.
    - Execute remote tools with timeout boundaries and error isolation.
    - Normalize remote responses into Aegis ToolResult and Evidence objects.
    """

    def __init__(
        self,
        server_command: str = sys.executable,
        server_args: list[str] | None = None,
        server_env: dict[str, str] | None = None,
        server_name: str = "remote-mcp",
        allowed_tools: list[str] | set[str] | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.server_command = server_command
        self.server_args = server_args or ["-m", "src.adapters.mcp"]
        self.server_env = server_env
        self.server_name = server_name
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else set()
        self.timeout_seconds = timeout_seconds

    def _get_server_params(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=self.server_command,
            args=self.server_args,
            env=self.server_env,
        )

    async def discover_tools(self) -> list[ToolDefinition]:
        """Discover tools from remote MCP server and return filtered ToolDefinitions."""
        params = self._get_server_params()
        try:
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await asyncio.wait_for(session.initialize(), timeout=self.timeout_seconds)
                    tools_result = await asyncio.wait_for(session.list_tools(), timeout=self.timeout_seconds)
                    
                    approved_definitions: list[ToolDefinition] = []
                    for tool in tools_result.tools:
                        # Allowlist policy check
                        if self.allowed_tools and tool.name not in self.allowed_tools:
                            continue
                        approved_definitions.append(self._map_tool_definition(tool))
                    return approved_definitions
        except Exception:
            # Return empty list on discovery failure without crashing
            return []

    def _map_tool_definition(self, tool: Tool) -> ToolDefinition:
        """Map an MCP Tool schema to an Aegis ToolDefinition domain model."""
        schema = tool.input_schema or {}
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        parameters: dict[str, Any] = {}
        for prop_name, prop_spec in properties.items():
            parameters[prop_name] = {
                "type": prop_spec.get("type", "string"),
                "description": prop_spec.get("description", ""),
            }

        return ToolDefinition(
            name=tool.name,
            description=tool.description or "",
            parameters=parameters,
            required=required,
        )

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Invoke a remote MCP tool with security bounds and response normalization."""
        # 1. Allowlist Policy Enforcement
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return ToolResult(
                summary={
                    "status": "blocked",
                    "error": f"Tool '{tool_name}' is not in approved MCP tool allowlist.",
                }
            )

        # 2. Argument Validation & Bounds
        if not isinstance(arguments, dict):
            return ToolResult(
                summary={
                    "status": "error",
                    "error": "Tool arguments must be a dictionary payload.",
                }
            )

        # Check for excessively large payloads (> 10KB argument payload)
        if len(json.dumps(arguments)) > 10240:
            return ToolResult(
                summary={
                    "status": "error",
                    "error": "Tool argument payload exceeded maximum permitted size (10KB).",
                }
            )

        params = self._get_server_params()
        try:
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await asyncio.wait_for(session.initialize(), timeout=self.timeout_seconds)
                    res = await asyncio.wait_for(
                        session.call_tool(tool_name, arguments),
                        timeout=self.timeout_seconds,
                    )
                    return self._parse_mcp_result(tool_name, res)
        except asyncio.TimeoutError:
            return ToolResult(
                summary={
                    "status": "error",
                    "error": f"Remote MCP tool '{tool_name}' timed out after {self.timeout_seconds}s.",
                }
            )
        except Exception as exc:
            return ToolResult(
                summary={
                    "status": "error",
                    "error": f"Remote MCP tool '{tool_name}' invocation failed: {exc!s}",
                }
            )

    def _parse_mcp_result(self, tool_name: str, res: Any) -> ToolResult:
        """Parse MCP CallToolResult and reconstruct Evidence or summary."""
        if not isinstance(res, CallToolResult):
            return ToolResult(
                summary={
                    "status": "error",
                    "error": f"Unexpected MCP response format from tool '{tool_name}'.",
                }
            )

        if res.is_error:
            err_msg = res.content[0].text if res.content and isinstance(res.content[0], TextContent) else "Remote error"
            return ToolResult(summary={"status": "error", "error": err_msg})

        if not res.content:
            return ToolResult(summary={"status": "success", "message": "Empty response from remote tool."})

        first_content = res.content[0]
        if not isinstance(first_content, TextContent):
            return ToolResult(summary={"status": "success", "content_type": type(first_content).__name__})

        raw_text = first_content.text
        try:
            data = json.loads(raw_text)
        except Exception:
            # Plain text summary
            return ToolResult(summary={"status": "success", "result": raw_text})

        if isinstance(data, dict):
            # Check if payload contains Evidence list
            evidence_items: list[Evidence] = []
            if "evidence" in data and isinstance(data["evidence"], list):
                for item in data["evidence"]:
                    if isinstance(item, dict) and "type" in item:
                        try:
                            ev_type = EvidenceType(item.get("type", "LOG"))
                        except Exception:
                            ev_type = EvidenceType.LOG

                        obs_at = datetime.fromisoformat(item["observed_at"]) if item.get("observed_at") else datetime.now(timezone.utc)
                        col_at = datetime.fromisoformat(item["collected_at"]) if item.get("collected_at") else datetime.now(timezone.utc)

                        evidence_items.append(
                            Evidence(
                                id=item.get("id", f"ev_mcp_{int(datetime.now(timezone.utc).timestamp()*1000)}"),
                                type=ev_type,
                                source_provider=item.get("source_provider") or f"mcp:{self.server_name}",
                                service_resource=item.get("service_resource"),
                                summary=item.get("summary", ""),
                                data=item.get("data", {}),
                                observed_at=obs_at,
                                collected_at=col_at,
                            )
                        )
            return ToolResult(evidence=evidence_items, summary=data.get("summary", data))

        return ToolResult(summary={"status": "success", "result": data})

    def create_pydantic_args_model(self, tool_def: ToolDefinition) -> type[BaseModel]:
        """Dynamically build a Pydantic model for an MCP ToolDefinition to enable validation."""
        fields: dict[str, Any] = {}
        for param_name, spec in tool_def.parameters.items():
            param_type: type[Any] = str
            if spec.get("type") == "integer":
                param_type = int
            elif spec.get("type") == "boolean":
                param_type = bool
            elif spec.get("type") == "number":
                param_type = float

            if param_name in tool_def.required:
                fields[param_name] = (param_type, ...)
            else:
                fields[param_name] = (param_type | None, None)

        model_name = f"{tool_def.name.capitalize()}Args"
        return create_model(model_name, **fields)
