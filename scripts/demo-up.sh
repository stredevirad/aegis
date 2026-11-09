#!/usr/bin/env bash
# Build and start the Aegis demo stack, seeded with four synthetic incidents.
#
#   scripts/demo-up.sh          Aegis only: http://localhost:8080 (about 0.4 GB of RAM)
#   scripts/demo-up.sh --live   + the live telemetry demo (instrumented services, Prometheus,
#                                 Jaeger, Loki, Alertmanager, flag service; about 2 GB more)
#
# DEMO MODE: development login and publicly known demo secrets; localhost only.
set -euo pipefail
cd "$(dirname "$0")/.."

args=(-f deploy/compose.yaml)
case "${1:-}" in
  "") ;;
  --live) args+=(--profile demo) ;;
  *) echo "usage: $0 [--live]" >&2; exit 2 ;;
esac

docker compose "${args[@]}" up -d --build --wait

echo
echo "Aegis is running: http://localhost:8080"
echo "Sign in with the development login: admin@example.com"
if [[ "${1:-}" == "--live" ]]; then
  echo "Live demo: wait ~3 minutes for a healthy baseline, then run scripts/demo-fault.sh"
fi
echo "Stop and remove everything: scripts/demo-down.sh"
