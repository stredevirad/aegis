# Build and start the Aegis demo stack, seeded with four synthetic incidents.
#
#   scripts\demo-up.ps1          Aegis only: http://localhost:8080 (about 0.4 GB of RAM)
#   scripts\demo-up.ps1 -Live    + the live telemetry demo (instrumented services, Prometheus,
#                                  Jaeger, Loki, Alertmanager, flag service; ~1 GB in total)
#
# DEMO MODE: development login and publicly known demo secrets; localhost only.
param([switch]$Live)
# "Continue", not "Stop": Windows PowerShell 5.1 turns docker's progress output on stderr into
# terminating errors when output is redirected. Failures are detected through $LASTEXITCODE.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

$composeArgs = @("-f", "deploy/compose.yaml")
if ($Live) { $composeArgs += @("--profile", "demo") }

docker compose @composeArgs up -d --build --wait
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose up failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Aegis is running: http://localhost:8080"
Write-Host "Sign in with the development login: admin@example.com"
if ($Live) {
    Write-Host "Live demo: wait ~3 minutes for a healthy baseline, then run scripts\demo-fault.ps1"
}
Write-Host "Stop and remove everything: scripts\demo-down.ps1"
