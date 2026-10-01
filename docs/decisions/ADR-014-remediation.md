# Title: Human-gated remediation with one real reversible action
# Status: Accepted
# Date: 2026-10-01
# Source: production-readiness audit; Council A (docs/councils/council-A-architecture.md)

## Decision
* Lifecycle: recommended -> proposed -> approved/rejected -> executing -> executed/failed -> verified/verification_failed -> (rolled back via the inverse action).
* Every proposal records action, target, parameters, risk, expected impact, required permission, reversibility and inverse, supporting evidence, and approval requirement.
* Execution is disabled per organization by default and enabled explicitly by an admin. Approval requires the `reviewer` role and, by default, a different person from the proposer. Execution is idempotent by proposal ID and never retried blindly.
* One action type is implemented end to end: setting a feature flag through a flag-service integration (the demo's reversible fault). Other actions appear only as recommendations (runbook text), never as stub executors.
* Post-action verification re-collects the target's signals and compares them with the incident window.
