> **Research-era document**, kept as a historical record of the research prototype (`src/`). The current product is described in [README.md](../README.md).

# Aegis — Reproducibility & Experiment Guide

This guide provides end-to-end instructions for reproducing all Aegis test suites, mock demonstrations, live OpenTelemetry Demo investigations, fault injection experiments, human annotation validation, and replay benchmarks.

---

## 1. Prerequisites & Environment Setup

### System Requirements
- **OS**: Windows (PowerShell), Linux, or macOS
- **Python**: 3.10+ (strict typing with `pydantic>=2.0.0`)
- **Docker**: Docker Desktop with Compose V2
- **Ollama** (optional, only needed for live LLM inference): with `qwen3.5:4b` model installed

### Python Dependencies Installation
Clone the repository and install dependencies in your virtual environment:

```powershell
# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1   # On Linux/macOS: source .venv/bin/activate

# Install runtime and development dependencies
pip install -e ".[dev]"
```

Verify the installation:
```powershell
pytest --version
mypy --version
ruff --version
```

---

## 2. Automated Test Suite & Static Verification

To run the complete automated test suite (165 collected; 162 passed; 3 skipped across 26 test files):

```powershell
# Full test suite execution
pytest tests/ -v

# Static type checking (strict mode across 54 source files)
mypy src/ tests/

# Linter and style checks
ruff check src/ tests/
```

### Key Sub-Test Suites
```powershell
# Human annotation agreement validation (Cohen's Kappa & Confusion Matrix)
pytest tests/test_human_annotation_validation.py -v -s

# Deterministic evidence replay tests
pytest tests/test_evidence_replay.py -v

# Evidence summarizer tests
pytest tests/test_evidence_summarizer.py -v

# Provider degradation & partial telemetry resilience
pytest tests/test_provider_degradation.py -v
pytest tests/test_partial_telemetry.py -v
```

---

## 3. Mock Investigation Demonstration (No External Dependencies)

To execute an end-to-end investigation with mock telemetry providers and mock LLM:

```powershell
python -m src.investigation.demo --mock-llm
```

**Expected Output**:
- Initializes SQLite repository (`aegis_demo.db`).
- Registers `MockMetricsProvider`, `MockLogsProvider`, and `MockTracesProvider`.
- Creates incident `scenario_payment_api_001`.
- Executes Fast Mode evidence collection, hypothesis generation, and verification.
- Concludes `[PLAUSIBLE] High latency in downstream-db caused payment-api to fail.`
- Records recommendation and state transition to `RECORDED`.

---

## 4. Live OpenTelemetry Demo Environment Setup

Aegis connects to the standard OpenTelemetry Astronomy Shop demo.

> **Fixture pinning (added by the 2026-10-01 production-readiness audit).** The research runs
> used the OpenTelemetry Demo at upstream commit `2e05c45b85b985a691cc75082c234e8d6ac0b2e9`
> **with a local edit** that deletes the `targeting` rule of the `productCatalogFailure` flag in
> `src/flagd/demo.flagd.json`. Upstream's rule returns `"off"` in both branches and overrides
> `defaultVariant`, so without the edit `scripts/inject-fault.py --flag productCatalogFailure
> --value on` has no effect. To reproduce the fixture exactly:
>
> ```powershell
> git clone https://github.com/open-telemetry/opentelemetry-demo.git external/opentelemetry-demo
> git -C external/opentelemetry-demo checkout 2e05c45b85b985a691cc75082c234e8d6ac0b2e9
> git -C external/opentelemetry-demo apply ../../config/otel-demo-fixture.patch
> ```

### 4.1 Starting the OTel Demo Stack
```powershell
# Start the Docker Compose stack (Prometheus, OpenSearch, Jaeger, Flagd, Microservices)
.\scripts\start-otel-demo.ps1
```
*Or directly via Docker Compose:*
```powershell
docker compose -f external/opentelemetry-demo/docker-compose.yml up -d
```

### 4.2 Verifying Telemetry Endpoints
Wait ~2 minutes for all services to become healthy, then run:
```powershell
.\scripts\verify-otel-demo.ps1
```

**Configured Endpoints**:
- **Application Web UI**: `http://localhost:8080` (or `http://localhost:56022`)
- **Prometheus Metrics**: `http://localhost:9090`
- **OpenSearch Logs**: `http://localhost:56695` (or `:9200`)
- **Jaeger Traces**: `http://localhost:56020` (or `:16686`)
- **Flagd Feature Flags**: `http://localhost:8080/feature`

---

## 5. Live Telemetry Investigation (Healthy Baseline)

Ensure Ollama is running locally with `qwen3.5:4b`:
```powershell
ollama run qwen3.5:4b
```

Run Aegis against the healthy live environment:
```powershell
python -m src.investigation.demo --live-telemetry
```

**Expected Behavior**:
- Collects 100+ real metric, log, and trace items from live backends.
- Formulates hypotheses and detects 0 error spikes.
- Concludes `NO_ANOMALY` or `INSUFFICIENT`.

---

## 6. Controlled Fault Injection Experiments

### 6.1 Inspecting Active Feature Flags
```powershell
python scripts/inject-fault.py --show
```

### 6.2 Injecting the `productCatalogFailure` Fault
```powershell
python scripts/inject-fault.py --flag productCatalogFailure --value on
```

Verify the fault signature in live telemetry:
- Navigate to `http://localhost:8080` and click on any product (e.g. "National Park Telescope").
- Observe HTTP 500 error page.
- Jaeger (`http://localhost:56020`) displays error spans with `rpc.grpc.status_code: 13`.

### 6.3 Running Autonomous Investigation on Injected Fault
```powershell
# Standard Generic Strategy (Summarizer ON)
python -m src.investigation.demo --live-telemetry --fault-scenario config/scenarios/product-catalog-failure.yaml

# Error-Focused Strategy (Phase 3.2 Comparison)
python -m src.investigation.demo --live-telemetry --fault-scenario config/scenarios/product-catalog-failure.yaml --evidence-strategy error_focused

# Summarizer OFF (Raw Bounded Mode Comparison)
python -m src.investigation.demo --live-telemetry --fault-scenario config/scenarios/product-catalog-failure.yaml --no-summarizer

# Neutral Incident Description Experiment (Phase 3.4)
python -m src.investigation.demo --live-telemetry --fault-scenario config/scenarios/product-catalog-failure-neutral.yaml
```

### 6.4 Automated Multi-Run Harness with RCA Evaluation
To run the automated batch orchestration script with automatic fault injection, traffic generation, differential measurement, LLM turns, and cleanup:

```powershell
python scripts/run-fault-investigation.py --runs 2 --scenario config/scenarios/product-catalog-failure.yaml
```

### 6.5 Restoring Healthy Baseline
```powershell
# Reset all fault flags to off
python scripts/inject-fault.py --recover-all

# Or run the complete reset script
.\scripts\reset-otel-demo.ps1
```

---

## 7. Evidence Snapshot & Offline Replay Experiments

Aegis supports deterministic offline replay to evaluate LLM prompts and scoring without spinning up live Docker clusters:

```python
from src.persistence.replay import EvidenceSnapshot, ReplayProvider, load_snapshot
from src.investigation.engine import InvestigationEngine

# 1. Load an exported snapshot
snapshot = load_snapshot("config/scenarios/product-catalog-failure.yaml") # or exported JSON snapshot

# 2. Instantiate ReplayProvider (implements Metrics, Logs, and Traces providers)
replay_provider = ReplayProvider(snapshot)

# 3. Execute investigation offline
# (Engine queries replay_provider instead of live backends)
```

Run replay verification tests:
```powershell
pytest tests/test_evidence_replay.py -v
```

---

## 8. Stopping the Environment

To stop the OpenTelemetry Demo containers when finished:
```powershell
.\scripts\stop-otel-demo.ps1
```
*Or directly via Docker Compose:*
```powershell
docker compose -f external/opentelemetry-demo/docker-compose.yml down
```
