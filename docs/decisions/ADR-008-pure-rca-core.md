# Title: Pure RCA core over explicit snapshots and windows
# Status: Accepted
# Date: 2026-10-01
# Source: production-readiness audit; Council A (docs/councils/council-A-architecture.md)

## Context
The research engine's worst defects share one cause: what data the engine was given, and from where, was never explicit (queries anchored to `now()`, global evidence IDs, span status discarded, citations invented after the fact).

## Decision
`aegis.core.analyze(snapshot, request) -> Analysis` is a pure function: no database, network or clock. Live collection (I/O) produces a `TelemetrySnapshot` for an explicit window; replay loads one from a file; both call the same function. Evidence IDs are content-derived and stable within a snapshot; persistence keys evidence by `(org_id, incident_id, evidence_id)`.

## Consequences
* Every production investigation can be replayed exactly (snapshots are persisted, subject to redaction and retention).
* The scenario benchmark tests the production code path, not a copy.
* Collection and analysis must agree on a versioned snapshot schema.
