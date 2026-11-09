#!/usr/bin/env bash
# Inject (or clear) the reversible fault in the live telemetry demo: the flag
# catalog.error_rate makes the catalog service fail a share of its requests. The flag service
# reports the change to Aegis's change feed; Prometheus alerts after about a minute; Alertmanager
# sends the alert to Aegis, which opens an incident and investigates it with live telemetry.
#
#   scripts/demo-fault.sh            set catalog.error_rate to 0.4
#   scripts/demo-fault.sh --reset    set it back to 0
#
# Needs the live demo: scripts/demo-up.sh --live
set -euo pipefail

value="0.4"
case "${1:-}" in
  "") ;;
  --reset) value="0" ;;
  *) echo "usage: $0 [--reset]" >&2; exit 2 ;;
esac

curl -fsS -X PUT "http://127.0.0.1:18081/flags/catalog.error_rate" \
  -H "Content-Type: application/json" \
  -d "{\"value\": \"${value}\", \"actor\": \"demo-fault\"}"
echo
echo "catalog.error_rate = ${value}"
