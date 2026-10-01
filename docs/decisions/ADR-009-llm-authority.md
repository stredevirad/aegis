# Title: LLM authority bounded by a deterministic confidence ceiling
# Status: Accepted
# Date: 2026-10-01
# Source: production-readiness audit; Council A (docs/councils/council-A-architecture.md)

## Context
Research found that small local models localize the failing service but do not deduce mechanisms, and the audit found that free-text parsing turned arbitrary model output into confident diagnoses. Prompt injection through telemetry can steer a model to cite real but irrelevant evidence, which ID validation alone cannot detect.

## Decision
* Deterministic analysis computes candidates, verification checks, a classification and a numeric confidence ceiling.
* The model adjudicates through schema-constrained JSON. It may choose a candidate (or none), explain the mechanism as typed claims with evidence IDs, and lower confidence or classification. It can never raise confidence above the ceiling or classification above the deterministic one. `CONFIRMED` requires passed deterministic checks.
* Choosing a non-leading candidate caps the result at `PLAUSIBLE` and is displayed as a disagreement.
* Invalid output gets one repair attempt, then the deterministic verdict stands, marked "AI assessment unavailable/invalid".
* Model reasoning ("thinking") is discarded; only structured claims are stored and shown.

## Consequences
Injection can at worst make the model *less* confident or choose another evidence-backed candidate at `PLAUSIBLE`. It cannot manufacture a confident diagnosis. The product works without any LLM.

## Amendment 1 (Council B, 2026-10-01)
* The confidence ceiling is an **ordinal support level** (`strong`, `moderate`, `weak`, `none`) computed by a lookup over classification, checks passed, checks not evaluable and coverage. No numeric probability is shown; no calibration claim is made before ~50 independently labelled cases.
* Corrected deterministic verdict rules:
  * **CONFIRMED** — at least two independent modalities; temporal precedence passed outside the tolerance band; propagation passed on real span status; isolation passed **or** a change whose scope matches the failure; valid baseline; zero failed checks; no contradicting evidence. Change alignment never substitutes for propagation.
  * **PLAUSIBLE** — the leader wins under the decision list, at least one evaluated check passed, no check failed, no contradicting evidence, and no downstream dependency on its failing path is unevaluated. Independent candidates that each satisfy this and are not on each other's paths are reported together (multi-fault).
  * **INSUFFICIENT** — anything else with anomalies present, including invalid baseline, failed checks, indistinguishable candidates on one path, or an unevaluated downstream dependency. Always names the next check.
  * **NO_ANOMALY** — adequate coverage, valid baseline, no anomaly, no edge change. Displayed as "Nothing found in what was checked" with the unchecked list; never displayed as "no anomaly" for a declared incident.
  * A missing change feed is a coverage gap: it caps the result at PLAUSIBLE and forbids the `change_induced` mechanism.
* The LLM is **off by default**; an organization admin enables it when an allowed provider exists. When enabled it narrates: typed claims over existing evidence IDs and a plain-language summary. No tool loop. Next checks are generated deterministically. A later AI downgrade is shown as a visible notice, never a silent change.
* Allowed claim: "model output cannot raise support above the deterministic level and cannot cite nonexistent evidence". Forbidden claims: "injection-proof", "injection-resistant", "cannot be fooled". The deterministic layer itself can be misled by forged telemetry; the defensible statement is "never more confident than the data".
