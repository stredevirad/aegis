Write-Host "Stopping OpenTelemetry Demo..."
Push-Location external/opentelemetry-demo
docker compose -f compose.yaml -f compose.observability.yaml down
Pop-Location
Write-Host "Demo stopped."
