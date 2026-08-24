$ErrorActionPreference = "Stop"
$OtelDir = Join-Path -Path $PSScriptRoot -ChildPath "..\external\opentelemetry-demo"

Write-Host "============================================================"
Write-Host "Resetting OpenTelemetry Demo Environment"
Write-Host "============================================================"

# 1. Restore flagd configuration to defaults
Write-Host "1. Restoring feature flags to defaults..."
$injectScript = Join-Path -Path $PSScriptRoot -ChildPath "inject-fault.py"
if (Test-Path $injectScript) {
    # Run the python script to recover all
    & python $injectScript --recover-all
} else {
    Write-Host "WARNING: inject-fault.py not found. Cannot reset flags."
}

# 2. Wait for flags to propagate
Start-Sleep -Seconds 5

# 3. Verify health
Write-Host "2. Verifying application health..."
$verifyScript = Join-Path -Path $PSScriptRoot -ChildPath "verify-otel-demo.ps1"
if (Test-Path $verifyScript) {
    & powershell -ExecutionPolicy Bypass -File $verifyScript
} else {
    Write-Host "WARNING: verify-otel-demo.ps1 not found."
}

# 4. Remove runtime database
Write-Host "3. Removing local Aegis database..."
$dbPath = Join-Path -Path $PSScriptRoot -ChildPath "..\aegis_demo.db"
if (Test-Path $dbPath) {
    Remove-Item -Path $dbPath -Force
    Write-Host "Removed $dbPath"
} else {
    Write-Host "Database $dbPath not found (already clean)."
}

Write-Host "============================================================"
Write-Host "Reset complete. System is in a clean baseline state."
Write-Host "============================================================"
