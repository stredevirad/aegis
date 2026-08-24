# Phase 2E-1 — Real Telemetry Healthy Baseline Investigation

## 1. Overview
Phase 2E-1 connects the real telemetry adapters (PrometheusProvider, OpenSearchProvider, JaegerProvider) to the Aegis InvestigationEngine running against the live OpenTelemetry Demo environment in `fast` mode with local LLM inference (`qwen3.5:4b`, `OLLAMA_THINK=false`).

No faults were injected during this baseline phase; the environment operated under standard healthy baseline traffic.

---

## 2. Telemetry Ingestion Architecture & Data Flow
```
                 OpenTelemetry Demo Services
                (frontend, productcatalog, cart, etc.)
                               |
               +---------------+---------------+
               |               |               |
               v               v               v
          Prometheus       OpenSearch        Jaeger
         (Port 9090)     (Port 56695)     (Port 56020)
               |               |               |
               v               v               v
       PrometheusProvider OpenSearchProvider JaegerProvider
               |               |               |
               +---------------+---------------+
                               |
                               v
                       InvestigationEngine
                         (Fast Mode)
                               |
                               v
                         OllamaProvider
                          (qwen3.5:4b)
                               |
                               v
                        SQLiteRepository
                         (aegis_demo.db)
```

---

## 3. Measured Live Investigation Execution
The healthy baseline investigation was executed via:
```powershell
python -m src.investigation.demo --live-telemetry
```

### Execution Metrics & Artifacts
- **Incident ID**: `inc_demo_1787555948`
- **Initial Telemetry Context**:
  - Prometheus Metrics: `5` series (e.g. `http_server_request_duration_seconds_count`)
  - OpenSearch Logs: `100` log records (`otel-logs-*`)
  - Jaeger Traces: `50` root trace summaries (`service=frontend`)
- **Total Evidence Collected**: `155` Evidence objects
- **Investigation Duration**: `7m 08s`
- **LLM Turns**: `3` turns
- **Tool Calls Executed**: `3` tool calls (Verification queries for service health and metrics)
- **Engine State Transitions**:
  1. `CREATED` -> `COLLECTING_EVIDENCE`
  2. `COLLECTING_EVIDENCE` -> `FORMING_HYPOTHESES`
  3. `FORMING_HYPOTHESES` -> `CONCLUDING`
  4. `CONCLUDING` -> `RECORDED`
- **Final Status**: `RECORDED`
- **Diagnosis Classification**: `INSUFFICIENT`
- **Diagnosis Output**:
  > `[INSUFFICIENT] Insufficient evidence to confirm a causal anomaly. The single high-latency trace (1783ms) may represent an isolated outlier or normal test traffic variance, as there is no concurrent evidence of elevated error rates, throughput degradation, or service health failures in the provided logs and metrics.`
- **Remediation Recommendation**:
  > `Monitor for sustained latency spikes or error rate increases (Reason: Correlation is not causation; a single high-latency event without supporting evidence of errors does not constitute a confirmed fault. Further investigation requires either repeated anomalies or direct evidence of system degradation.)`

---

## 4. Persistence Verification
The entire investigation lifecycle was verified in SQLite (`aegis_demo.db`):
- `incidents` table: 1 incident stored with `status='RECORDED'`, `diagnosis_classification='INSUFFICIENT'`.
- `evidence` table: 155 evidence records stored with full source provider provenance, timestamps, and JSON data.
- `hypotheses` table: Formulated hypotheses with confidence scores and evidence linkings.
- `recommendations` table: 1 structured recommendation saved with action, reason, expected impact, and risk assessment.

---

## 5. Key Verification Findings
1. **Generic Query Abstraction**: InvestigationEngine requests domain-level evidence concepts without leaking PromQL or Lucene syntax into LLM prompts.
2. **Context Boundedness**: Traces and logs are aggressively summarized before prompt formatting, keeping LLM input token usage well within local context budgets.
3. **Sound Baseline Judgment**: The LLM recognized that normal traffic variance in a healthy system does not warrant a false-positive fault diagnosis, correctly classifying the incident as `INSUFFICIENT` / `NO_ANOMALY`.
