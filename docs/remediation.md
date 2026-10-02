# Aegis — Remediation

Aegis can execute **one kind of action**: setting a feature flag on an operator-registered flag service (ADR-014). Everything else it recommends is advice for humans (next checks). Execution is **disabled per organization by default** (`remediation_enabled`).

## Lifecycle

```
proposed ──approve──▶ approved ──execute──▶ executing ──▶ verifying ──▶ verified
    │                    │                     │                └──────▶ verification_failed
    └──reject──▶ rejected └──(expiry)──▶ expired └──▶ failed
executed / verifying / verified / verification_failed ──rollback──▶ rolled_back | rollback_failed
```

| Step | Who | What is checked |
|---|---|---|
| Catalog | admin (`remediation.manage`) | Entry names a flag-service **integration** of this organization, an exact flag key, the target value, the service it applies to, risk and expected impact. |
| Suggest | system | Deterministic: catalog entries whose service is a root cause of the latest completed investigation. The model never proposes. |
| Propose | investigator/admin | Parameters are copied from the catalog (requests cannot supply them). Aegis reads the flag's **prior state** (value + version); the inverse (rollback) is "set back to the prior value". Optional evidence citations must exist in the investigation. One open proposal per action and incident. |
| Approve | reviewer/admin | **A different person** from the proposer (two-person rule; `remediation_allow_self_approval` can relax it per organization). The flag is read again and must still equal the prior state (else the proposal expires as stale). The approval stores `sha256(proposal, params hash, prior state)` and expires after `AEGIS_REMEDIATION_APPROVAL_MINUTES` (15). The inverse is approved together with the forward action. |
| Execute | investigator/admin | Organization must have `remediation_enabled`. Compare-and-set in the database: the row moves to `executing` only if it is `approved`, unexpired, and its approval hash and parameter hash still match values **recomputed from the row** (a replayed, expired or tampered approval matches nothing). The flag is re-read and must equal the approved prior state; the write uses the flag service's own compare-and-set (`if_version`). **Never retried**: an uncertain outcome (timeout) is recorded as failed for a human to inspect. |
| Verify | worker | After `AEGIS_REMEDIATION_VERIFY_DELAY_SECONDS` (120) the worker re-collects the target service's telemetry and compares the incident window with the post-action window (error ratio and p95 latency, with numbers). Synthetic diagnoses, missing telemetry or too little traffic are `not_evaluable`. Recovered → `verified`; otherwise `verification_failed`. The incident is never closed automatically. |
| Roll back | investigator/admin | Uses the pre-approved inverse; compare-and-set against the state the execution produced; once. |

The approval screen shows the literal diff: flag key, current value, new value, rollback value. Every step writes an audit record (`remediation.proposed/approved/rejected/executed/verified/rolled_back`) with the diff and hashes.

## Trust boundary

The two-person rule protects against a single compromised or careless account. It does not protect against the deployment operator or the identity provider (`security-model.md` §2). MCP is never an execution path.

## Demo flag service

`demo/app/flags.py`: `GET /flags/{key}`, `PUT|POST /flags/{key}` with `{"value", "actor", "if_version"}` (409 on version mismatch). Register it as a `flag_service` connector; it reports its own changes to Aegis as change events.
