# Stop the Aegis demo stack (core and live demo) and delete its containers and volumes
# (database, ingest keys). Images are kept, so the next demo-up is faster.
# "Continue", not "Stop": Windows PowerShell 5.1 turns docker's progress output on stderr into
# terminating errors when output is redirected. Failures are detected through $LASTEXITCODE.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")
docker compose -f deploy/compose.yaml --profile demo down -v --remove-orphans
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose down failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}
