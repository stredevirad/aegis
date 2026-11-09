> **Research-era document (2026-08)**, kept as a historical record of the research prototype (`src/`). The current product is described in [README.md](../README.md).

# Resource Strategy

## Hardware Context
* **Target System**: 16GB consumer laptop without assuming a high-end discrete GPU.
* **LLM**: Local execution using `Ollama` with the `qwen3.5:9b` model (6.6GB disk, heavily quantized for VRAM efficiency).

## LLM Strategy
* **Development Mode (`OLLAMA_THINK=false`)**: Fast responses, fewer tool calls, smaller evidence window.
* **Research/Deep Mode (`OLLAMA_THINK=true`)**: More extensive reasoning, wider evidence gathering. Not the default for standard issues.
* Do NOT run multiple large models simultaneously to avoid Out-Of-Memory (OOM) errors.

## Docker Resource Management
* Inspect RAM and port availability before launching any test fixtures.
* The full OpenTelemetry demo stack is heavyweight. Run only necessary subsets when possible.
* Use graceful startup and shutdown scripts to ensure no orphaned containers drain resources.

## Backend Dependencies
* Use **SQLite** for persistence rather than PostgreSQL/Redis to reduce the standing memory footprint.
* Use Python standard library asynchronous tools for background tasks rather than Kafka or Celery.
* Prefer lightweight implementations for continuous monitoring (e.g., standard statistics or EWMA) over continuous LLM inference.
