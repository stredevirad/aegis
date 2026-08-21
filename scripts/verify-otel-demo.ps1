Write-Host "Verifying OpenTelemetry Demo environment..."

$allPass = $true

function Verify-Endpoint {
    param(
        [string]$Name,
        [string]$Url,
        [scriptblock]$CheckBlock
    )
    Write-Host "Checking $Name ($Url)..." -NoNewline
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -ErrorAction Stop
        if ($CheckBlock) {
            $result = & $CheckBlock $response
            if ($result) {
                Write-Host " OK" -ForegroundColor Green
            } else {
                Write-Host " FAIL (Condition not met)" -ForegroundColor Red
                $global:allPass = $false
            }
        } else {
            if ($response.StatusCode -lt 400 -or $response.StatusCode -eq 404 -or $response.StatusCode -eq 405) {
                Write-Host " OK ($($response.StatusCode))" -ForegroundColor Green
            } else {
                Write-Host " FAIL ($($response.StatusCode))" -ForegroundColor Red
                $global:allPass = $false
            }
        }
    } catch {
        # OTel collector returns 405 Method Not Allowed on GET for /v1/metrics sometimes, which throws WebException in PS
        if ($_.Exception.Response.StatusCode -eq 405 -or $_.Exception.Response.StatusCode -eq 404) {
            Write-Host " OK ($($_.Exception.Response.StatusCode))" -ForegroundColor Green
        } else {
            Write-Host " FAIL ($($_.Exception.Message))" -ForegroundColor Red
            $global:allPass = $false
        }
    }
}

Verify-Endpoint -Name "Frontend Application" -Url "http://localhost:8080/"
Verify-Endpoint -Name "OTel Collector" -Url "http://localhost:4318/v1/metrics"

Verify-Endpoint -Name "Prometheus Metrics" -Url "http://localhost:9090/api/v1/query?query=up" -CheckBlock {
    param($r)
    $content = $r.Content | ConvertFrom-Json
    return ($content.status -eq "success" -and $content.data.result.Count -gt 0)
}

Verify-Endpoint -Name "OpenSearch Logs" -Url "http://localhost:9200/otel-logs-*/_search?size=1" -CheckBlock {
    param($r)
    $content = $r.Content | ConvertFrom-Json
    return ($content.hits.total.value -gt 0)
}

Verify-Endpoint -Name "Jaeger Traces API" -Url "http://localhost:16686/api/services" -CheckBlock {
    param($r)
    $content = $r.Content | ConvertFrom-Json
    return ($content.data -contains "frontend")
}

if (-not $allPass) {
    Write-Host "Verification failed. Not all components are healthy or populated with telemetry." -ForegroundColor Red
    exit 1
} else {
    Write-Host "Verification passed. Environment is healthy and telemetry is flowing." -ForegroundColor Green
}
