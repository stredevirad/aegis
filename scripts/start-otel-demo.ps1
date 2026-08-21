Write-Host "Starting OpenTelemetry Demo (Minimal + Observability)..."
Push-Location external/opentelemetry-demo
docker compose -f compose.yaml -f compose.observability.yaml up -d
Pop-Location
Write-Host "Demo starting. Port mappings:"
Write-Host "  - Frontend: http://localhost:8080"
Write-Host "  - Prometheus (Metrics): http://localhost:9090"
Write-Host "  - OpenSearch (Logs): http://localhost:9200"
Write-Host "  - Jaeger UI (Traces): http://localhost:16686"
Write-Host "  - OTel Collector: http://localhost:4318 / grpc://localhost:4317"
