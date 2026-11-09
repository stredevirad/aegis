# Aegis — 3–5 Minute Demonstration Script

A step-by-step walkthrough for showing Aegis to an interviewer or reviewer, using the one-command stack. The research-era script for the frozen prototype is kept at the end.

## Before the demo (once)

```bash
scripts/demo-up.sh                 # Windows: scripts\demo-up.ps1  →  http://localhost:8080
```

To also show the live path, start with `scripts/demo-up.sh --live` at least 3 minutes before the demo, so the services have some history. Close other heavy applications; the live profile uses about 1 GB.

## 1. The problem (30 s)

> "When a microservice breaks, an on-call engineer has to collect metrics, logs and traces and work out which service caused it. LLM tools sound confident even when the evidence is thin. Aegis decides deterministically from cited evidence, refuses to guess when the evidence is weak, and lets an LLM only explain or lower confidence."

## 2. A confirmed root cause (60 s)

1. Sign in at http://localhost:8080 with `admin@example.com` (development login).
2. Dashboard → **Recent incidents** → "Checkout error rate above 5% on the storefront" → **Investigations** → **Open**.
3. Point at the verdict: **Strongly supported**, root cause **catalog**, mechanism **change-induced (feature flag)**.
4. Point at the checks: baseline valid, propagation (failing traces end in catalog), isolation, change scope (a flag change 0.5 min before onset), modality agreement. Each one cites evidence.
5. Click an evidence ID such as `ev-change-…`: it scrolls to and highlights the cited item. Then open the **Evidence explorer**.

> "Every claim is a link to evidence. Nothing is asserted without a citation."

## 3. It refuses to guess (60 s)

1. Incidents → "Frontend errors rising (edge metrics only)" → open its investigation.
2. **Not enough evidence**, **NO FINDING**, and concrete **next checks** (what frontend depends on, failing traces, recent changes), plus the coverage gaps that caused the abstention.
3. Incidents → "Frontend 5xx spike with suspicious error log messages". The logs contain injected text claiming `payments` is the root cause. The verdict still names **catalog**, and in the evidence list the injected text is shown as plain data.

> "Abstention is a first-class answer. Injected text in telemetry is data, never instructions."

## 4. The live path (optional, 90 s; needs `--live`)

1. Run `scripts/demo-fault.sh`. It sets `catalog.error_rate=0.4` on the demo flag service, and the change lands in Aegis's change feed.
2. Within about a minute, Prometheus fires `HighErrorRate`, Alertmanager posts to Aegis, incidents open for catalog and frontend, and each is investigated automatically from live Prometheus, Jaeger and Loki data.
3. Show the catalog incident's verdict. In the recorded run (2026-11-09) it was **PLAUSIBLE, root cause catalog, feature-flag mechanism**, and the engine said why it wasn't CONFIRMED: no valid 60-minute baseline on a freshly started stack.
4. Run `scripts/demo-fault.sh --reset`.

## 5. Under the hood (60 s, pick two)

- PostgreSQL row-level security with `FORCE`, and pooled-connection leak tests (`backend/tests/db/test_pool_isolation.py`).
- The job queue on `SKIP LOCKED` with leases and reaping (`backend/aegis/jobs/queue.py`).
- SSRF-safe egress: DNS pinning, deny ranges, no redirects (`backend/aegis/integrations/egress.py`).
- The LLM validator: it can only lower confidence (`backend/aegis/llm/validate.py`, ADR-009).
- CI: 360 backend tests (134 on real PostgreSQL), 3 browser E2E tests against this stack, and gitleaks.

## Afterwards

`scripts/demo-down.sh` removes the containers and volumes.

---

## Research-era demo (2026-08, frozen prototype)

> Kept as a historical record. It drives the research prototype in `src/` (OpenTelemetry Demo, Ollama, SQLite), not the current product.

This script provides a concise, step-by-step walkthrough for demonstrating Aegis to technical interviewers, colleagues, or research reviewers.

---

### Prerequisites (Before Demo Begins)
1. Terminal 1: Docker stack running (`.\scripts\start-otel-demo.ps1`).
2. Terminal 2: Ollama running with `qwen3.5:4b` (`ollama run qwen3.5:4b`).
3. Terminal 3: Active repository environment (`.venv\Scripts\Activate.ps1`).

---

### Demonstration Sequence

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

### Key Talking Points Summary
1. **Clean Architecture**: Domain core has zero external adapter dependencies.
2. **Pre-LLM Summarization**: Prevents false negatives that occur when context is naively truncated.
3. **Error-Focused Efficiency**: Yields 2.3x faster execution and 59% less evidence noise.
4. **Honest Evaluation**: Distinguishes between symptom restatement and true causal deduction.
