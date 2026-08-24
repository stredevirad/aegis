# Phase 2A — OpenTelemetry Environment

## Purpose
This document describes the external OpenTelemetry Demo environment used as a realistic telemetry fixture for Aegis development. Phase 2A focuses solely on deploying, verifying, and documenting this environment without coupling it to the core Investigation Engine.

## Fixture
- **Location**: `external/opentelemetry-demo/`
- **Configuration**: Minimal deployment with observability stack overlay.

## Current Upstream Revision
- **Target**: `external/opentelemetry-demo` (Currently cloned locally)
- **Compose Files**: `compose.yaml` (core services) and `compose.observability.yaml` (telemetry backends)
- **Default Image Version**: `3.0.0` (as per `.env`)

## Deployment
The environment is brought up using:
```powershell
docker compose -f compose.yaml -f compose.observability.yaml up -d
```

## Application
- **Name**: Frontend
- **Host Endpoint**: `http://localhost:<ephemeral_port>` (Resolved dynamically by `verify-otel-demo.ps1`)
- **Health**: OK (Returns HTTP 200)

## Collector
- **Service**: `otel-collector` (OpenTelemetry Collector Contrib)
- **Endpoint**: Configured internally via `OTEL_COLLECTOR_HOST` in the Docker network.
- **Health**: OK
- **Telemetry Path**: The collector receives data via OTLP and exports to Prometheus, OpenSearch, and Jaeger.

## Metrics
- **Backend**: Prometheus
- **Host Endpoint**: `http://localhost:9090` (Static map `9090:9090`)
- **Backend Health Verification**: Checked via `/-/healthy`.
- **Actual Telemetry Verification**: Queried `/api/v1/query?query=http_server_request_duration_seconds_count` and verified that active application metrics are flowing into the backend.
- **Sample Metric**: `http_server_request_duration_seconds_count`

## Logs
- **Backend**: OpenSearch
- **Host Endpoint**: `http://localhost:<ephemeral_port>`
- **Backend Health Verification**: Queried `/_cluster/health` and received `status: yellow/green`.
- **Actual Telemetry Verification**: Queried the `otel-logs-*` index via `/_search?size=1` and successfully located an application log.

## Traces
- **Backend**: Jaeger
- **Host Endpoint**: `http://localhost:<ephemeral_port>` (UI mapped port)
- **Backend Health Verification**: Queried `/jaeger/ui/api/services` and received HTTP 200.
- **Actual Telemetry Verification**: Queried `/jaeger/ui/api/traces?service=frontend&limit=1` and confirmed a real application trace (with traceID) exists for the frontend service.

## Port Map
Because the upstream compose files use ephemeral ports for several observability backends to avoid conflicts, ports are resolved dynamically at runtime. The `verify-otel-demo.ps1` script automatically detects these and writes them to a connection profile.
- **Prometheus**: 9090
- **OpenSearch**: Ephemeral (e.g., 59358)
- **Jaeger UI/API**: Ephemeral (e.g., 59364)
- **Frontend**: Ephemeral (e.g., 60144)

## Connection Profile
A lightweight connection profile is generated automatically and stored at:
`config/opentelemetry-demo.yaml`

## Start / Verify / Stop
Project-owned scripts manage the lifecycle safely without interfering with other host Docker resources:
- **Start**: `powershell .\scripts\start-otel-demo.ps1`
- **Verify**: `powershell .\scripts\verify-otel-demo.ps1`
- **Stop**: `powershell .\scripts\stop-otel-demo.ps1`

## Resource Usage
- **Containers Running**: 25
- **Aggregate Memory**: ~2.0 GB - 3.0 GB Total (Fits comfortably within 16.7 GB RAM host).
- **CPU**: Low at idle, periodic spikes due to the `load-generator` (5 VUs).
- **Disk**: Adequate free disk space remaining on the Windows-SSD (C:). 

## Known Limitations
- The demo uses ephemeral host ports for OpenSearch and Jaeger. The `verify-otel-demo.ps1` script circumvents this by rewriting `config/opentelemetry-demo.yaml` on every successful verification.
- OpenSearch takes longer to initialize than other containers; verification may fail if run immediately after start.

## Next Phase
**Phase 2B Recommendation**:
Based on the discovered environment, the **PrometheusProvider** should be implemented first. The Prometheus metric endpoint (`:9090`) is static, stable, and actively collecting rich application and infrastructure metrics (like `system_cpu_time_seconds_total` and `http_server_duration_milliseconds_count`). This makes it the ideal first adapter to connect Aegis to real-world data.
