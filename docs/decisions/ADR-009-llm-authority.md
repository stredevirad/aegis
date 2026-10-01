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
