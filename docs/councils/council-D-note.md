# Council D — Deployment / readiness (note)

**Date:** 2026-10-02 · **Protocol:** installed `llm-council` skill (5 advisors started).

## What happened

Council D was convened before deployment work, as the roadmap required ("Council D before
deployment work"). Five advisors were started, but the council's synthesis was not preserved,
so no verdict document exists. This note records only what can still be verified.

## Known outcomes (committed in 8b5fbad, 2026-11-09)

1. **Per-integration LLM circuit breaker.** The breaker is keyed per integration
   (`integration:<id>`). Client 4xx errors other than 429 (bad key, bad request) are classed as
   `client_error` and no longer trip it. One tenant's misconfigured key can't
   open the breaker for other tenants. Code: `backend/aegis/llm/providers.py`,
   `backend/aegis/llm/adjudicate.py`. Tests: `backend/tests/llm/test_prompt_and_providers.py`.
2. **Reconciling stranded remediations.** `reconcile_stuck()` in
   `backend/aegis/remediation/runner.py` finds proposals stuck in `executing`/`rolling_back`
   after a crash. It re-reads the target and marks them `failed` (or `rollback_failed` for a
   stranded rollback) with `uncertain: true`. It never re-executes. Migration `0008_remediation_sweep` adds the leased, `SKIP LOCKED`
   claim function the worker's maintenance loop uses. Tests: `backend/tests/api/test_remediation.py`.

## Superseded

The remaining deployment questions (Helm/Kubernetes, supply-chain hardening, load and chaos
testing) were superseded by the 2026-11-08 portfolio re-scope. That re-scope keeps a
one-command Docker Compose stack plus CI and defers the rest with reasons. See
[council-P1-portfolio-scope.md](council-P1-portfolio-scope.md) and
[council-P2-agent-tooling.md](council-P2-agent-tooling.md).
