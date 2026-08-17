# Evidence Model

The `Evidence` model represents a single, verifiable observation from a telemetry source.

## Schema
- `id`: Unique identifier
- `type`: Category (`METRIC`, `LOG`, `TRACE`, `DEPLOYMENT`, `TOPOLOGY`, `UNKNOWN`)
- `source_provider`: Origin of the evidence (e.g., `prometheus`, `jaeger`)
- `service_resource`: Optional service/resource identifier
- `summary`: Human-readable summary
- `data`: Structured payload specific to the evidence type (see conventions below)
- `observed_at`: When the event occurred (Timezone-Aware)
- `collected_at`: When Aegis ingested it (Timezone-Aware)

## Timezone-Aware Timestamps Policy
Aegis strictly enforces timezone-aware datetimes for all core temporal fields (e.g., `Evidence.observed_at`, `Evidence.collected_at`, `Incident.start_time`, `Incident.end_time`).
This is enforced using Pydantic's `AwareDatetime`. Naive datetimes raise a `ValidationError`. This guarantees predictable timelines across all investigation boundaries.

### Temporal Semantics
- `observed_at`: When the event actually happened in the external system.
- `collected_at`: When Aegis retrieved/ingested the evidence.

## Evidence Data Conventions
The `data` field is intentionally flexible (`dict[str, Any]`), but adapters SHOULD use these conventional keys per evidence type to enable cross-provider comparisons:

| EvidenceType | Conventional Keys |
|---|---|
| `METRIC` | `metric_name`, `value`, `labels`, `unit` |
| `LOG` | `message`, `level`, `structured_fields` |
| `TRACE` | `trace_id`, `span_id`, `operation`, `duration_ms`, `status_code` |
| `DEPLOYMENT` | `version`, `deployer`, `changed_files` |
| `TOPOLOGY` | `source`, `target`, `relationship_type` |

These are conventions, not schema enforcement. Adapters may include additional keys.

## Service Health Model
The system uses a structured `ServiceHealth` model to express high-level service status instead of unstructured dictionaries:
- `service_id`: The service in question
- `status`: `HEALTHY`, `DEGRADED`, `UNAVAILABLE`, `UNKNOWN` (uses unified `HealthStatus` enum)
- `summary`: Optional context
- `observed_at`: Timezone-aware timestamp
- `metadata`: Optional arbitrary data
