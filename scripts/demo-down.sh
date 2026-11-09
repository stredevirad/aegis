#!/usr/bin/env bash
# Stop the Aegis demo stack (core and live demo) and delete its containers and volumes
# (database, ingest keys). Images are kept, so the next demo-up is faster.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose -f deploy/compose.yaml --profile demo down -v --remove-orphans
