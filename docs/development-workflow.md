# Development Workflow

## Project Lifecycle Scripts
Aegis will eventually rely on structured scripts to manage the local environment cleanly:
* `start-dev`: Starts core Aegis services and SQLite.
* `stop-dev`: Gracefully stops Aegis services.
* `verify-dev`: Validates local dependency and port health.

## OpenTelemetry Demo (Fixture)
* Do not modify the demo repository itself (treat as read-only). Use overlays, wrappers, or patches for fault injection.
* Ensure integration verification makes actual requests to the local endpoints instead of assuming `docker compose up` worked.

## Agent Guidelines for Antigravity
* When errors occur, inspect the actual error, identify the root cause (e.g., code vs configuration), and document non-obvious workarounds.
* Use `docs/decisions` for ADRs when making significant architectural choices.
* Do not delete unrelated user files or aggressively prune Docker resources.
* Do not introduce new dependencies without a resource cost evaluation (RAM, CPU, disk, startup time).

## Evaluation
Evaluation is a first-class feature of Aegis.
`evaluation/` will contain scenarios, baselines, and metrics to track LLM diagnostic accuracy, latency, and false positive rates over time.
