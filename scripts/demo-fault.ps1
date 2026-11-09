# Inject (or clear) the reversible fault in the live telemetry demo: the flag
# catalog.error_rate makes the catalog service fail a share of its requests. The flag service
# reports the change to Aegis's change feed; Prometheus alerts after about a minute; Alertmanager
# sends the alert to Aegis, which opens an incident and investigates it with live telemetry.
#
#   scripts\demo-fault.ps1           set catalog.error_rate to 0.4
#   scripts\demo-fault.ps1 -Reset    set it back to 0
#
# Needs the live demo: scripts\demo-up.ps1 -Live
param([switch]$Reset)
$ErrorActionPreference = "Stop"

$value = if ($Reset) { "0" } else { "0.4" }
$body = @{ value = $value; actor = "demo-fault" } | ConvertTo-Json -Compress
$result = Invoke-RestMethod -Method Put -Uri "http://127.0.0.1:18081/flags/catalog.error_rate" `
    -ContentType "application/json" -Body $body
$result | ConvertTo-Json -Compress
Write-Host "catalog.error_rate = $value"
