# Aegis Requirements

## Project Identity
Aegis is a general, evidence-grounded incident investigation and root-cause analysis engine for software systems. It is intended as a student/research project optimized for correctness, reproducibility, and explainability.

## Core Directives
1. **Generality**: Aegis must not be tightly coupled to a specific demonstration application (e.g., Astronomy Shop). Concepts must be generic (service, endpoint, metric, trace, etc.).
2. **Evidence-Grounded AI**: The LLM must not invent telemetry. Every claim must trace to a verifiable evidence object.
3. **Local First**: Development and initial research use a local Ollama model (`qwen3.5:9b`). Cloud LLMs are optional and for later comparison.
4. **Resource Consciousness**: Target environment is a 16GB consumer laptop without assuming a high-end discrete GPU. Keep the database lightweight (SQLite initially) and background processing simple (no Kafka/RabbitMQ in V1).
5. **Read-Only Remediation**: V1 will recommend remediation actions, but will not automatically execute operational changes.

## Investigation Loop
The agent will execute a discrete state machine:
`INCIDENT_CREATED -> COLLECTING_EVIDENCE -> FORMING_HYPOTHESES -> VERIFYING_HYPOTHESES -> CONCLUDING -> RECOMMENDATION -> RECORDED`

Terminal/error state: `FAILED` (covers timeouts, LLM errors, and insufficient evidence).

## Tooling (V1 Planned)
The LLM can request predefined Aegis tools:
* `get_service_health`
* `query_metrics`
* `get_metric_anomalies`
* `search_logs`
* `get_trace`
* `get_related_traces`
* `get_service_dependencies`
* `search_previous_incidents`
* `get_deployment_metadata`

The system must support permission boundaries (Automatic, User Confirmation, Human Approval).

## Temporal Convention
All core temporal fields use timezone-aware datetimes (`AwareDatetime`). Naive datetimes are rejected at validation time.

- `observed_at`: when the event actually happened in the external system.
- `collected_at`: when Aegis retrieved/ingested the evidence.
- `start_time` / `end_time`: investigation boundary timestamps.
