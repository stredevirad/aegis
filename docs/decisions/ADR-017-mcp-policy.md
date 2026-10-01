# Title: MCP in the product: off by default, read-only, pinned
# Status: Accepted
# Date: 2026-10-01
# Source: Council C; audit findings S-H5, S-M2, S-M4, S-L1, S-L2

## Decision
* MCP integration is disabled by default per organization.
* Only read-only tools may be allowlisted. Remediation never goes through MCP.
* The allowlist is fail-closed (empty means nothing). Each allowlisted tool's name, description and input schema are **pinned by hash** at approval time; drift disables the tool until an admin re-approves it. Built-in tool names are reserved.
* Remote MCP servers are connectors in the registry (ADR-016); remote transport is streamable HTTP over the egress client; sessions are persistent per worker with timeouts and reconnection.
* Arguments are validated against the pinned schema with size caps; results are size-capped; evidence returned by a tool gets an Aegis-generated ID and `source_provider = "mcp:<connector>"` regardless of what the server claims.
* The research MCP server (`src/adapters/mcp`) stays as-is in the research layer. A product MCP server exposing tenant-scoped, read-only incident and evidence tools authenticated by API key is planned (roadmap M8).
