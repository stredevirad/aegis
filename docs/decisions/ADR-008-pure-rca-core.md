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

## Amendment 1 (Council B, 2026-10-01)
* Candidate ranking is an **ordered decision list**: each rule is one sentence, evaluated in order, and every feature value is logged in the analysis output. A service cannot lead the ranking without its own evidence (it is a trace error origin, it has a change, or its edges changed).
* Every verification check is **tri-state** (`pass`, `fail`, `not_evaluable`) with a reason; `not_evaluable` never counts toward a classification.
* Additional stages/checks: per-edge call-count and topology diff between baseline and incident (masked faults, fallbacks); change-scope match; baseline validity (fallback windows: before the most recent change, same time on the previous day; otherwise not evaluable); saturation from resource metrics; temporal precedence with a tolerance band (scrape interval + clock skew; ties are not evaluable); MAD floor and minimum event counts for low-traffic signals; log signatures are counted, never interpreted, and a "new signature" requires a minimum count from more than one source instance.
* Mechanism class is a deterministic lookup over which signal moved and the change type.
* The output always includes a coverage report ("what was not checked") and, when not `CONFIRMED`, the cheapest next check that would separate the leading hypotheses.
