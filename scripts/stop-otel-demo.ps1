$ErrorActionPreference = "Stop"
$OtelDir = Join-Path -Path $PSScriptRoot -ChildPath "..\external\opentelemetry-demo"

Write-Host "============================================================"
Write-Host "Stopping Minimal OpenTelemetry Demo"
Write-Host "============================================================"
Write-Host "Target: $OtelDir"

Push-Location $OtelDir
try {
    Write-Host "Bringing down core services + observability stack..."
    docker compose -f compose.yaml -f compose.observability.yaml down
    
    Write-Host "============================================================"
    Write-Host "OpenTelemetry Demo Stopped!"
    Write-Host "============================================================"
} finally {
    Pop-Location
}
