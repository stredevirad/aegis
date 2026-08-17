# Title: Provider Capability Discovery
# Status: Accepted
# Date: 2026-08-17

## Context
Aegis must remain functional across different environments where some telemetry sources (e.g., distributed traces) may be unavailable. The investigation engine cannot assume all providers are present or healthy.

## Decision
Introduce a `ProviderStatus` model and mandate that every provider implement a `get_status()` method. This allows the system to explicitly query supported capabilities and health at runtime.

## Consequences
- **Positive**: Aegis can degrade gracefully during an investigation, formulating hypotheses based only on available capabilities.
- **Negative**: Providers must implement discovery logic instead of purely data retrieval interfaces.
