$ErrorActionPreference = "Stop"
$OtelDir = Join-Path -Path $PSScriptRoot -ChildPath "..\external\opentelemetry-demo"

Write-Host "============================================================"
Write-Host "Verifying Minimal OpenTelemetry Demo"
Write-Host "============================================================"

Push-Location $OtelDir
try {
    # Dynamically find ports since the demo uses some ephemeral host ports
    $FrontendPort = (docker compose -f compose.yaml -f compose.observability.yaml port frontend 8080) -replace ".*:",""
    $PrometheusPort = (docker compose -f compose.yaml -f compose.observability.yaml port prometheus 9090) -replace ".*:",""
    $OpenSearchPort = (docker compose -f compose.yaml -f compose.observability.yaml port opensearch 9200) -replace ".*:",""
    $JaegerPort = (docker compose -f compose.yaml -f compose.observability.yaml port jaeger 16686) -replace ".*:",""

    # 1. Verify Application (Frontend)
    Write-Host "1. Verifying Application (Frontend on port $FrontendPort)..."
    $appResponse = Invoke-WebRequest -Uri "http://localhost:$FrontendPort" -UseBasicParsing
    if ($appResponse.StatusCode -ne 200) {
        Write-Error "Frontend is not reachable!"
    }
    Write-Host "   Frontend is UP."

    # 2. Verify Prometheus (Metrics)
    Write-Host "2. Verifying Prometheus (Metrics on port $PrometheusPort)..."
    
    # Check Backend Health
    $promHealth = Invoke-WebRequest -Uri "http://localhost:$PrometheusPort/-/healthy" -UseBasicParsing -ErrorAction SilentlyContinue
    if ($promHealth.StatusCode -ne 200) {
        Write-Error "Prometheus backend health check failed!"
    }
    Write-Host "   [+] Backend Health: OK"

    # Check Actual Application Metric
    $promResponse = Invoke-WebRequest -Uri "http://localhost:$PrometheusPort/api/v1/query?query=http_server_request_duration_seconds_count" -UseBasicParsing
    $promJson = $promResponse.Content | ConvertFrom-Json
    if ($promJson.status -ne "success" -or $promJson.data.result.Count -eq 0) {
        Write-Error "Prometheus is not returning application metric data for http_server_request_duration_seconds_count!"
    }
    Write-Host "   [+] Application Telemetry: Found http_server_request_duration_seconds_count."

    # 3. Verify OpenSearch (Logs)
    Write-Host "3. Verifying OpenSearch (Logs on port $OpenSearchPort)..."
    
    # Check Backend Health
    $osHealth = Invoke-WebRequest -Uri "http://localhost:$OpenSearchPort/_cluster/health" -UseBasicParsing -ErrorAction SilentlyContinue
    if ($osHealth.StatusCode -ne 200) {
        Write-Error "OpenSearch backend health check failed!"
    }
    Write-Host "   [+] Backend Health: OK"

    # Check Actual Application Log
    $osResponse = Invoke-WebRequest -Uri "http://localhost:$OpenSearchPort/otel-logs-*/_search?size=1" -UseBasicParsing
    $osJson = $osResponse.Content | ConvertFrom-Json
    if ($osJson.hits.total.value -eq 0) {
        Write-Error "OpenSearch is not returning any application logs in otel-logs-*!"
    }
    Write-Host "   [+] Application Telemetry: Found logs in otel-logs-*."

    # 4. Verify Jaeger (Traces)
    Write-Host "4. Verifying Jaeger (Traces on port $JaegerPort)..."
    
    # Check Backend Health (via services endpoint)
    $jaegerHealth = Invoke-WebRequest -Uri "http://localhost:$JaegerPort/jaeger/ui/api/services" -UseBasicParsing -ErrorAction SilentlyContinue
    if ($jaegerHealth.StatusCode -ne 200) {
        Write-Error "Jaeger backend health check failed!"
    }
    Write-Host "   [+] Backend Health: OK"

    # Check Actual Application Trace
    $jaegerResponse = Invoke-WebRequest -Uri "http://localhost:$JaegerPort/jaeger/ui/api/traces?service=frontend&limit=1" -UseBasicParsing
    $jaegerJson = $jaegerResponse.Content | ConvertFrom-Json
    if ($jaegerJson.data.Count -eq 0 -or -not $jaegerJson.data[0].traceID) {
        Write-Error "Jaeger is not returning any actual traces for the frontend service!"
    }
    Write-Host "   [+] Application Telemetry: Found traces for frontend service."

    Write-Host "============================================================"
    Write-Host "Verification Complete: All endpoints reachable!"
    Write-Host "============================================================"

    # Export configuration automatically for convenience
    $config = @"
name: opentelemetry-demo
environment:
  type: docker
telemetry:
  metrics:
    type: prometheus
    endpoint: http://localhost:$PrometheusPort
  logs:
    type: opensearch
    endpoint: http://localhost:$OpenSearchPort
  traces:
    type: jaeger
    endpoint: http://localhost:$JaegerPort
  topology:
    type: static
"@
    $configPath = Join-Path -Path $PSScriptRoot -ChildPath "..\config\opentelemetry-demo.yaml"
    Set-Content -Path $configPath -Value $config
    Write-Host "Updated connection profile at $configPath"
} finally {
    Pop-Location
}
