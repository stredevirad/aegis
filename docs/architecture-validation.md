# Architecture Validation Checklist

This document details the critical architectural constraints for the Aegis project. Automated static checks (e.g., using `importlinter` or custom pytest fixtures) should be implemented to enforce these rules.

## Mandatory Rules

1. **Adapter Isolation**: `src/domain/` and `src/investigation/` MUST NOT import anything from `src/adapters/`.
2. **LLM Agnosticism**: `src/domain/` and `src/investigation/` MUST NOT directly import `ollama` or refer to specific models like `qwen3.5:9b`.
3. **Application Neutrality**: The core codebase MUST NOT contain hard-coded references to the OpenTelemetry Demo (e.g., "cart-service", "frontend").
4. **Fixture Isolation**: The external OpenTelemetry demo (`external/opentelemetry-demo/`) MUST NOT be imported by Aegis core code.
5. **Provider Boundaries**: Providers MUST return normalized `Evidence` models, not raw JSON payloads from Prometheus, Loki, or Tempo.
