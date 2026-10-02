#!/usr/bin/env bash
# Backend quality gate (also used by CI). Fails on the first failing step.
# Usage: scripts/check.sh [extra pytest args]   e.g. scripts/check.sh -m "not live"
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen pytest -q -p no:cacheprovider "$@"
uv run --frozen python -m aegis.benchmark.run --scenarios ../eval/scenarios --gate > /dev/null
echo "check: all steps passed"
