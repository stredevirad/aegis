#!/usr/bin/env bash
# Local development API + worker (dev login, synthetic scenario sources).
#
#   docker compose -f deploy/compose.dev.yaml --profile app up -d --wait   # Postgres on 55433
#   backend/scripts/dev-stack.sh                                             # API on 127.0.0.1:8000
#
# Creates organization "demo" with an invitation for admin@example.com (log in with that
# email through the SPA's development login) and seeds four synthetic demo incidents.
# Development credentials only.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${AEGIS_DEV_PG_PORT:-55433}"
DB="${AEGIS_DEV_DB:-aegis_dev}"
OWNER_PW=dev-owner-password-123
APP_PW=dev-app-password-123
export AEGIS_PROFILE=development AEGIS_AUTH_MODE=dev AEGIS_LOG_JSON=false
export AEGIS_SCENARIO_DIR="$(cd ../eval/scenarios && pwd)"
export AEGIS_PUBLIC_BASE_URL="${AEGIS_PUBLIC_BASE_URL:-http://127.0.0.1:8000}"
export AEGIS_ALLOWED_ORIGINS='["http://127.0.0.1:8000","http://localhost:8000","http://localhost:5173","http://127.0.0.1:5173"]'
uv run --frozen python -m aegis.db.bootstrap --superuser-url "postgresql://postgres:devsuperuser@127.0.0.1:${PORT}/postgres" \
  --owner-password "$OWNER_PW" --app-password "$APP_PW" --database "$DB"
export AEGIS_MIGRATION_DATABASE_URL="postgresql+asyncpg://aegis_owner:${OWNER_PW}@127.0.0.1:${PORT}/${DB}"
export AEGIS_DATABASE_URL="postgresql+asyncpg://aegis_app:${APP_PW}@127.0.0.1:${PORT}/${DB}"
uv run --frozen aegis migrate upgrade
uv run --frozen aegis admin create-org --slug demo --name "Demo" --admin-email admin@example.com
# Synthetic demo incidents with queued scenario investigations (idempotent; the worker runs them).
uv run --frozen aegis admin seed-demo --org demo
uv run --frozen aegis worker &
WORKER=$!
trap 'kill $WORKER 2>/dev/null || true' EXIT
uv run --frozen aegis api --host 127.0.0.1 --port 8000
