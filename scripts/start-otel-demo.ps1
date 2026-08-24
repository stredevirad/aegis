$ErrorActionPreference = "Stop"
$OtelDir = Join-Path -Path $PSScriptRoot -ChildPath "..\external\opentelemetry-demo"

Write-Host "============================================================"
Write-Host "Starting Minimal OpenTelemetry Demo"
Write-Host "============================================================"
Write-Host "Target: $OtelDir"

Push-Location $OtelDir
try {
    Write-Host "Bringing up core services + observability stack..."
    docker compose -f compose.yaml -f compose.observability.yaml up -d
    
    Write-Host "============================================================"
    Write-Host "OpenTelemetry Demo Started!"
    Write-Host "============================================================"
    Write-Host "Wait roughly ~30-60 seconds for containers to initialize."
    Write-Host ""
    Write-Host "Expected Endpoints:"
    Write-Host "- Application : http://localhost:8080"
    Write-Host "- Prometheus  : http://localhost:9090"
    Write-Host "- Jaeger UI   : http://localhost:16686"
    Write-Host "- OpenSearch  : http://localhost:9200"
    Write-Host "- Grafana     : http://localhost:3000"
    Write-Host "============================================================"
} finally {
    Pop-Location
}
