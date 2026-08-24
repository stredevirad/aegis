# Phase 2F — Integration Hardening & Degradation Resilience

## 1. Overview
Phase 2F hardens the Aegis runtime, domain models, investigation engine, telemetry adapters, and persistence against partial outages, provider degradation, malformed LLM outputs, schema roundtripping, and non-deterministic environments.

---

## 2. Hardening Test Suite (80 / 80 Passing)

The full test suite covers 80 automated unit, integration, mock, degradation, provenance, and persistence tests:

```powershell
pytest tests/ -v
================== 80 passed, 4 warnings in 81.82s ==================
```

### Breakdown of Test Suites
| Test Suite | Test Count | Focus Area | Status |
|---|---|---|---|
| `tests/test_domain_models.py` | 20 | Domain invariants, timezone checks, enum integrity, models | PASSED |
| `tests/test_engine.py` | 7 | Engine state transitions (fast & iterative), budget bounds, tool validation | PASSED |
| `tests/test_evidence_provenance.py` | 3 | Verification of Evidence provenance & metadata for all 3 telemetry sources | PASSED |
| `tests/test_jaeger_provider.py` | 6 | Jaeger adapter health checks, trace queries, limit enforcement, error handling | PASSED |
| `tests/test_jaeger_integration.py` | 1 | Live Jaeger integration test against running demo cluster | PASSED |
| `tests/test_opensearch_provider.py` | 8 | OpenSearch adapter health, time-window validation, query DSL, error handling | PASSED |
| `tests/test_opensearch_integration.py` | 1 | Live OpenSearch integration test against running demo cluster | PASSED |
| `tests/test_prometheus_provider.py` | 7 | Prometheus adapter health, PromQL query range, time-window validation | PASSED |
| `tests/test_prometheus_integration.py` | 1 | Live Prometheus integration test against running demo cluster | PASSED |
| `tests/test_ollama_integration.py` | 2 | Ollama local API connectivity and message serialization | PASSED |
| `tests/test_llm_failure.py` | 2 | LLM error handling, prompt timeouts, malformed JSON recovery | PASSED |
| `tests/test_provider_degradation.py` | 12 | Single/multi provider outage handling, empty telemetry responses | PASSED |
| `tests/test_partial_telemetry.py` | 5 | Investigation engine resilience with partial telemetry availability | PASSED |
| `tests/test_sqlite_roundtrip.py` | 3 | SQLite persistence roundtrip for healthy, fault, and failed investigations | PASSED |

---

## 3. Degradation Matrix & Failure Modes Tested

### 1. Telemetry Provider Outages
- **Metrics Down / Logs & Traces Up**: Engine logs warning, continues investigation using available logs & traces, and completes lifecycle to `RECORDED`.
- **Logs Down / Metrics & Traces Up**: Engine queries metrics and traces, forms hypotheses, and reaches conclusion.
- **Traces Down / Metrics & Logs Up**: Engine continues without trace context.
- **All Telemetry Down**: Engine records provider health failure, formulates `INSUFFICIENT` diagnosis, and safely records state without unhandled exceptions.

### 2. Empty Telemetry Handling
- When queries return zero metrics series, empty log hits, or empty trace arrays, providers return structured empty `ToolResult` / `Evidence` without throwing KeyError or IndexError.

### 3. LLM Parsing & Malformed Response Resilience
- Case-insensitive, robust regex extraction for hypotheses, diagnoses, classifications (`CONFIRMED`, `PLAUSIBLE`, `NO_ANOMALY`, `INSUFFICIENT`), and remediation recommendations.
- Markdown headers (`**Hypothesis:**`, `### Diagnosis`, bulleted lists) are cleanly parsed into structured domain objects.

### 4. Database Invariant Preservation
- SQLite schema migrations are idempotent.
- Datetime fields are strictly stored with timezone awareness (UTC ISO-8601).
- Foreign key and cascade relationships properly maintain incident, hypothesis, evidence, event, and recommendation linkings.

---

## 4. Reset & Cleanup Infrastructure
- **Script**: `scripts/reset-otel-demo.ps1`
  - Restores all `flagd` feature flags to defaults (`--recover-all`).
  - Verifies application frontend (56022/8080), Prometheus (9090), OpenSearch (56695), and Jaeger (56020).
  - Removes local demo runtime database (`aegis_demo.db`).
  - Verified working seamlessly on Windows PowerShell.
