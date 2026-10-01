# Title: PostgreSQL job queue, LLM slot pool, fairness and live updates
# Status: Accepted
# Date: 2026-10-01
# Source: production-readiness audit; Council A (docs/councils/council-A-architecture.md)

## Context
Investigations take seconds (deterministic) to minutes (LLM on CPU). Expected scale: a few organizations, tens of concurrent investigations, alert bursts of hundreds per minute, one CPU-bound local model.

## Decision
* Jobs live in PostgreSQL, created transactionally with the investigation, claimed with `FOR UPDATE SKIP LOCKED` through a narrow claim function; leases + heartbeats; bounded, jittered retries of retry-safe failures only; cancellation flag; idempotency keys and a one-active-investigation-per-incident constraint.
* A per-organization running-job cap provides fairness; a queue-depth limit defers new automatic investigations (recorded, never dropped).
* A global LLM slot pool (lease rows) bounds concurrent model calls across workers.
* Live updates: SSE endpoint that polls `investigation_events` once per second; REST polling fallback. No LISTEN/NOTIFY until measurement shows a need.
* No Redis, Celery or Temporal.

## Consequences
One stateful dependency to run, back up and secure. If measurement later shows queue or SSE contention, LISTEN/NOTIFY or Redis can be introduced behind the same interfaces.
