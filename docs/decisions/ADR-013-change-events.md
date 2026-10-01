# Title: Change events as first-class evidence
# Status: Accepted
# Date: 2026-10-01
# Source: production-readiness audit; Council A (docs/councils/council-A-architecture.md)

## Context
Research Phase 3 found the model "cannot deduce the feature-flag mechanism", but the flag change was never in the evidence. Change correlation without a data source would ship as an empty stage.

## Decision
`/api/v1/change-events` accepts deploy, config, feature-flag and rollout events (ingest-scoped API key). The Kubernetes integration contributes rollout events. The demo flag service posts its own changes. The core treats changes on a candidate service or its dependencies shortly before anomaly onset as evidence for change-induced hypotheses, and absence of changes as information, not as contradiction.
