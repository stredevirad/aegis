# Phase 2A — OpenTelemetry Environment

## Purpose
Establish a lightweight, reproducible OpenTelemetry environment using the official upstream `opentelemetry-demo` fixture. This environment provides the real telemetry backends (metrics, logs, traces) required to build Aegis telemetry adapters in Phase 2B, without introducing heavyweight cloud infrastructure or redesigning Aegis Phase 1 core logic.

## Fixture
The official OpenTelemetry Demo located in `external/opentelemetry-demo/`.

## Current Upstream Revision
- Git Commit: `2e05c45b85b985a691cc75082c234e8d6ac0b2e9`

## Deployment
Deployed using the official core minimal footprint and the observability profile.
Command used:
```bash
docker compose -f compose.yaml -f compose.observability.yaml up -d
```

## Application
- **Name**: Frontend Proxy / Frontend
- **Endpoint**: `http://localhost:8080/`
- **Health**: OK (Verified via HTTP GET)

## Collector
- **Service**: `otel-collector`
- **Endpoint**: `http://localhost:4318/v1/metrics`
- **Health**: OK (Verified via HTTP 405 response on GET)
- **Telemetry Path**: Application -> OTel Collector -> Jaeger (Traces), Prometheus (Metrics), OpenSearch (Logs).

## Metrics
- **Backend**: Prometheus
- **Endpoint**: `http://localhost:9090`
- **Health**: OK
- **Actual Verification**: `http://localhost:9090/api/v1/query?query=up` returns success with valid data.
- **Sample Metric**: `up` (Prometheus target health).

## Logs
- **Backend**: OpenSearch
- **Endpoint**: `http://localhost:9200`
- **Health**: OK
- **Actual Verification**: `http://localhost:9200/otel-logs-*/_search?size=1` returns > 0 hits.
- **Sample Log Metadata**: OTel formatted logs with `trace_id` and `span_id`.

## Traces
- **Backend**: Jaeger
- **Endpoint**: `http://localhost:16686`
- **Health**: OK
- **Actual Verification**: `http://localhost:16686/api/services` returns a list containing `frontend`.
- **Sample Trace Metadata**: Service `frontend` traces visible in the Jaeger UI.

## Port Map
| Component | Host Port | Internal Port |
| --- | --- | --- |
| Application (Frontend) | 8080 | 8080 |
| OTel Collector (HTTP) | 4318 | 4318 |
| OTel Collector (gRPC) | 4317 | 4317 |
| Metrics (Prometheus) | 9090 | 9090 |
| Logs (OpenSearch) | 9200 | 9200 |
| Traces (Jaeger UI) | 16686 | 16686 |

## Connection Profile
Located at `config/opentelemetry-demo.yml`.

## Start / Verify / Stop
- **Start**: `powershell -ExecutionPolicy Bypass -File .\scripts\start-otel-demo.ps1`
- **Verify**: `powershell -ExecutionPolicy Bypass -File .\scripts\verify-otel-demo.ps1`
- **Stop**: `powershell -ExecutionPolicy Bypass -File .\scripts\stop-otel-demo.ps1`

## Resource Usage
- **Containers**: ~22
- **Memory**: The Docker engine limits total RAM. The `compose` files set specific resource limits on containers, typically utilizing ~4GB RAM total.
- **Disk**: Docker images take ~5GB of disk space. Free disk space on host (C: drive) was closely monitored during deployment.

## Known Limitations
- The deployment launches all microservices in the demo because they are tightly coupled in the official `compose.yaml`. This demands significant system resources.

## Next Phase
**Phase 2B** should implement the first concrete telemetry adapter for Aegis, based on this environment.
Recommendation: Implement the **Metrics Provider** via **Prometheus** first. Prometheus is fully functioning on port 9090 and offers deterministic numeric signals that will allow the `InvestigationEngine` to confidently query anomalies.
