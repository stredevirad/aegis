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

## Amendment 1 (Council C, 2026-10-01)
* The model never chooses remediation parameters. Proposals reference an entry in an **organization-maintained action catalog** (e.g. flag key + allowed target value + environment); the deterministic layer selects candidate catalog entries, the LLM may at most reference a catalog ID.
* The approval binds a SHA-256 of the exact parameters **and the target's prior state** and expires (default 15 minutes). Execution is a compare-and-set: `UPDATE ... SET status='executing' WHERE id=$1 AND status='approved' AND params_hash=$2 AND expires_at > now() RETURNING id`; the executor re-reads the target's current state and aborts if it differs from the approved prior state.
* The approval screen shows the literal key, current value and new value — never only the model's description.
* The inverse action is approved together with the forward action, so rollback during the incident does not wait for a second approval round.
* The two-person rule protects against a single compromised or careless account; it does not protect against the operator or identity provider (documented trust boundary).
