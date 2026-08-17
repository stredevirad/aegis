# Aegis Architecture

## Core Architectural Layers
The architecture adheres to a strict dependency direction:

```
Presentation (UI)
    ↓
Application / Investigation (Agent Loop)
    ↓
Domain (Interfaces, Normalized Models)
    ↑
Adapters / Infrastructure (Concrete Implementations)
    ↑
External Systems (Prometheus, Loki, Docker, Ollama)
```

## Guiding Principles
1. **Domain Isolation**: The `domain/` layer contains only normalized models and interfaces. It must not import `adapters/` or external SDKs like `prometheus_client` or `ollama`.
2. **Normalized Evidence**: All telemetry is transformed into normalized `Evidence` models before being passed to the `investigation/` layer. The investigation engine does not care if the source was Loki or Jaeger.
3. **Provider Extensibility**: New telemetry sources or environments are added by creating a new module in `adapters/` implementing the relevant provider interfaces (e.g., `MetricsProvider`).
4. **SQLite Persistence**: Investigation history, hypotheses, and collected evidence are persisted in an SQLite database using an ORM to allow future migration to PostgreSQL.

## Application State Machine
The core `investigation/` engine operates an explicit agent loop without relying on opaque, heavyweight frameworks. State transitions are deliberate and auditable.
