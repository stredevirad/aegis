# Aegis — 3–5 Minute Live Demonstration Script

This script provides a concise, step-by-step walkthrough for demonstrating Aegis to technical interviewers, colleagues, or research reviewers.

---

## Prerequisites (Before Demo Begins)
1. Terminal 1: Docker stack running (`.\scripts\start-otel-demo.ps1`).
2. Terminal 2: Ollama running with `qwen3.5:4b` (`ollama run qwen3.5:4b`).
3. Terminal 3: Active repository environment (`.venv\Scripts\Activate.ps1`).

---

## Demonstration Sequence

### [0:00 - 0:45] Step 1: Environment & Baseline Verification
**Presenter Narrative**:
> *"Aegis is an evidence-grounded incident investigation engine. We connect to a live microservices deployment—the OpenTelemetry Astronomy Shop—instrumented with Prometheus, OpenSearch, and Jaeger. First, let's verify our baseline environment is healthy."*

**Command**:
```powershell
python scripts/inject-fault.py --show
```
*Show that all feature flags are `off` (healthy baseline).*

---

### [0:45 - 1:30] Step 2: Inject Controlled Fault & Generate Real Traffic
**Presenter Narrative**:
> *"Now we inject a controlled failure into the `product-catalog` service via Flagd. This causes gRPC errors in the backend and HTTP 500 errors on product pages."*

**Commands**:
```powershell
# Inject product catalog failure
python scripts/inject-fault.py --flag productCatalogFailure --value on

# Verify that product endpoints now return HTTP 500
Invoke-WebRequest -Uri "http://localhost:8080/api/products/OLJCESPC7Z" -SkipHttpErrorCheck | Select-Object StatusCode
```
*Show HTTP 500 response.*

---

### [1:30 - 3:00] Step 3: Run Aegis Autonomous Investigation
**Presenter Narrative**:
> *"We launch Aegis using our error-focused strategy and pre-LLM evidence summarizer. Aegis will query Prometheus, OpenSearch, and Jaeger, extract diagnostic features, form hypotheses, execute verification tool calls, and produce a grounded diagnosis."*

**Command**:
```powershell
python -m src.investigation.demo --live-telemetry --fault-scenario config/scenarios/product-catalog-failure.yaml --evidence-strategy error_focused
```

**Walk Through Output Elements on Screen**:
1. **Telemetry Collected**: Show metrics, error logs, and Jaeger trace spans collected into normalized `Evidence` objects.
2. **Diagnostic Summary**: Point out the error signals and latency percentiles surfaced to the model.
3. **Hypotheses Formed**: Show competing hypotheses (e.g. *Upstream product-catalog failure* vs *Frontend logic error*).
4. **Verification Tool**: Show the engine executing `query_error_metrics` to confirm upstream 5xx status codes.
5. **Diagnosis Produced**: Show the final diagnosis:
   `[PLAUSIBLE] Upstream dependency product-catalog status is UNKNOWN, correlating with HTTP 500 errors...`
6. **Remediation Recommendation**: Show actionable recommendation with cited supporting evidence IDs.

---

### [3:00 - 3:45] Step 4: Audit Trail & Database Persistence
**Presenter Narrative**:
> *"Every state transition, evidence object, hypothesis, and recommendation is persisted to SQLite with full provenance. Let's inspect the database audit trail."*

**Command**:
```powershell
python -c "import sqlite3; conn = sqlite3.connect('aegis_demo.db'); print(conn.execute('SELECT action, summary FROM events ORDER BY timestamp DESC LIMIT 5').fetchall())"
```
*Show the logged events (`RECOMMENDATION_PRODUCED`, `DIAGNOSIS_PRODUCED`, `TOOL_EXECUTION`, `STATE_TRANSITION`).*

---

### [3:45 - 4:15] Step 5: Automated RCA Evaluation & Incident-Leak Check
**Presenter Narrative**:
> *"Aegis includes an automated RCA evaluator that tests for incident leaks (penalizing prompt restatements) and mechanism alignment. Let's run our test suite."*

**Command**:
```powershell
pytest tests/test_human_annotation_validation.py -v -s
```
*Show 83.3% agreement and Cohen's $\kappa = 0.778$ against the 12-case reference benchmark.*

---

### [4:15 - 4:45] Step 6: Fault Recovery & Baseline Restoration
**Presenter Narrative**:
> *"Finally, we restore the environment to a healthy baseline and verify system recovery."*

**Command**:
```powershell
python scripts/inject-fault.py --recover-all
Invoke-WebRequest -Uri "http://localhost:8080/api/products/OLJCESPC7Z" | Select-Object StatusCode
```
*Show `StatusCode: 200`.*

---

## Key Talking Points Summary
1. **Clean Architecture**: Domain core has zero external adapter dependencies.
2. **Pre-LLM Summarization**: Prevents false negatives that occur when context is naively truncated.
3. **Error-Focused Efficiency**: Yields 2.3x faster execution and 59% less evidence noise.
4. **Honest Evaluation**: Distinguishes between symptom restatement and true causal deduction.
