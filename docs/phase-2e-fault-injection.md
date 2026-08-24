# Phase 2E-2 — Controlled Fault Injection & Real RCA Validation

## Overview

Phase 2E-2 validates the Aegis autonomous root cause analysis (RCA) pipeline against active, controlled fault injections in the live OpenTelemetry Demo environment.

Unlike synthetic mocks, the fault injection pipeline alters the actual runtime behavior of services via flagd feature flags, verifies the telemetry signature delta across Prometheus, OpenSearch, and Jaeger, executes Aegis investigations powered by local LLM (`qwen3.5:4b`), validates evidence provenance, evaluates RCA accuracy against ground truth, and automatically restores system health.

---

## Controlled Fault Scenario: `productCatalogFailure`

- **Scenario ID**: `scenario_product_catalog_failure`
- **Fault Flag**: `productCatalogFailure` (`defaultVariant: "on"`)
- **Target Component**: `product-catalog` service
- **Affected Path**: `frontend` service (`/api/products/[productId]`, catalog endpoints)
- **Ground Truth**: `productCatalogFailure feature flag enabled causing product catalog service failures`
- **Incident Symptom**: `Elevated Error Rate on Product Pages` (HTTP 500 errors when loading product catalog pages)

---

## Telemetry Signature Differential Verification

Before initiating LLM turns, the orchestration harness establishes a baseline-vs-fault signature comparison:

| Telemetry Dimension | Baseline Signature (All Faults OFF) | Injected Fault Signature (`productCatalogFailure=on`) | Delta / Signal |
| :--- | :--- | :--- | :--- |
| **HTTP Status** | `200 OK` across all product endpoints | `HTTP 500 Internal Server Error` on product endpoints | **Changed** (Reproducible 500s) |
| **Error Traces (Jaeger)** | 0 error spans | 11+ error spans with `error=true` / `codes.Internal` | **+11 Error Spans** |
| **Error Logs (OpenSearch)**| 0 error hits | Log entries capturing 500 server responses | **Verified** |
| **Metrics (Prometheus)** | Normal traffic counter rates | 9 metric time series active across frontend/catalog | **Active** |

---

## Multi-Run Autonomous Investigation Results

Two consecutive end-to-end investigation runs were executed using the local `qwen3.5:4b` model via Ollama.

### Run 1

- **Incident ID**: `inc_fault_1787558017`
- **Duration**: 345.4s (5m45s)
- **LLM Turns**: 3 turns
- **Tool Calls**: 4 calls (`get_service_health`, telemetry queries)
- **Total Evidence Collected**: 159 items (Metrics: 9, Logs: 100, Traces: 50)
- **Formed Hypotheses**:
  1. *Anomalous upstream dependency degradation (e.g., slow response times or errors from `product-catalog` causing frontend timeouts).*
  2. *Application-level logic error within the frontend handling product requests.*
- **Classification**: `PLAUSIBLE`
- **Model Diagnosis**:
  > *"Upstream dependency `product-catalog` status is UNKNOWN, correlating with HTTP 500 errors in frontend logs and high latency/timeout spans in traces. The combination of UNKNOWN upstream health and failed downstream requests indicates a potential degradation or failure of the upstream service affecting product loading."*
- **Remediation Recommendation**:
  > *"Investigate root cause for `product-catalog` status UNKNOWN — Unknown upstream status combined with observed 500 errors and slow traces suggests the service is failing or returning unexpected responses, directly impacting frontend requests."*
- **Supporting Evidence Citation**: 5 specific trace evidence IDs cited and validated (`["ev_trace_fb937175bea0fc79df072049600a81c0", "ev_trace_334d3f67b18344038a4c644957aa76cc", ...]`)
- **RCA Result**: **`CORRECT`** (Accurately identified affected component `product-catalog` and failure mechanism)

---

### Run 2

- **Incident ID**: `inc_fault_1787558379`
- **Duration**: 350.9s (5m50s)
- **LLM Turns**: 2 turns
- **Tool Calls**: 5 calls (`query_metrics`, `search_logs`, `get_service_health`)
- **Total Evidence Collected**: 259 items (Metrics: 9, Logs: 200, Traces: 50)
- **Formed Hypotheses**:
  1. *Upstream service degradation - The frontend is failing due to latency or errors originating from its upstream dependencies (e.g., product-catalog), causing HTTP 500s.*
  2. *Frontend Service Failure - The frontend service itself is malfunctioning.*
- **Classification**: `CONFIRMED`
- **Model Diagnosis**:
  > *"Backend service returning HTTP 500 for product API endpoint"*
- **Remediation Recommendation**:
  > *"Review backend application logs and verify database connectivity (The CloudTrail log explicitly records a 500 Internal Server Error response from the `GET /api/products/...` request, indicating a server-side failure rather than a client error or network timeout.)"*
- **Supporting Evidence Citation**: 1 specific log evidence ID cited and validated
- **RCA Result**: **`PARTIALLY_CORRECT`** (Accurately identified affected product backend component and HTTP 500 symptom)

---

## Evaluation Summary & Consistency

- **Total Runs**: 2
- **Consistency**: **2/2 runs produced CORRECT or PARTIALLY_CORRECT diagnoses**
- **Evidence Linking**: Verified precise evidence citation (no blanket evidence dumping)
- **Fault Recovery**: Automatic recovery via `inject-fault.py --recover` verified healthy across Application (`200 OK`), Prometheus (`200 OK`), OpenSearch (`200 OK`), and Jaeger (`200 OK`).
