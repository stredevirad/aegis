# Aegis — Production Roadmap

**Status legend:** `DONE` (acceptance criteria met and verified as stated) · `IN PROGRESS` · `PLANNED` · `PARTIAL` (some criteria met; the gap is stated) · `BLOCKED` (external dependency; the workaround is stated).
Status is updated in the same commit that changes it. "Verified" means a command was run and its output recorded in `production-readiness.md`; nothing else counts.

Build order follows Council A: core-first vertical slice → wrap the slice with persistence, jobs, API and UI → LLM → breadth. Councils B (RCA) and C (security) run before the core contract and the first migration are written; Council D before deployment work; Council E on the release candidate.


## Portfolio release scope (2026-11-08)

On 2026-11-08 the remaining roadmap was re-scoped to a **portfolio release** ([Council P1](councils/council-P1-portfolio-scope.md)). The milestone sections below keep their original acceptance criteria. This table is the current status, matching the ledger ([`production-readiness.md`](production-readiness.md)) and [`PORTFOLIO_PROGRESS.md`](PORTFOLIO_PROGRESS.md).

| Milestone | Status | Reason / evidence |
|---|---|---|
| M0 Audit, decisions, research preservation | DONE | Licence chosen (MIT) 2026-11-09 |
| M1 Pure RCA core + benchmark gate | DONE | Benchmark 15/15 in CI on every push |
| M2 Persistence, jobs, API, auth, tenancy, audit, ingestion | DONE | 134 PostgreSQL tests in CI (`AEGIS_REQUIRE_DB=1`, none skipped) |
| M3 LLM adjudication | PARTIAL | Adapters, validator and fallback done; no live LLM output has been accepted (1B model rejected); hosted-model runs DEFERRED (owner-held keys; stretch S1); cost accounting DEFERRED |
| M4 Live integrations | PARTIAL | Prometheus, Loki/OpenSearch, Jaeger and the change feed work live; the one-command live demo produced a PLAUSIBLE catalog verdict (2026-11-09). Tempo and Kubernetes collectors DEFERRED (not needed for the demo) |
| M5 Web application | PARTIAL (portfolio scope met) | Operator UI against the real backend; 78 unit tests plus 3 Playwright E2E tests in CI. Remediation UI DEFERRED (stretch S3) |
| M6 Remediation and notifications | PARTIAL | Two-person remediation lifecycle with verification and rollback (backend/API, tested). The two reconciler robustness gaps found by the 2026-11-09 security review were fixed in Phase 6. Slack/PagerDuty/webhook notifications DEFERRED (not needed to show RCA) |
| M7 Containers, compose stack, reproducible demo | DONE | Backend image, one-command stack (`scripts/demo-up`), seeded demo, live profile; E2E in CI against the stack |
| M8 Self-observability, retention, MCP, topology | DEFERRED | Retention sweeps, topology graph and production MCP client hardening are cut; a read-only MCP server is stretch S4 |
| M9 Kubernetes packaging and CI/CD | PARTIAL | CI/CD part done (GitHub Actions, gitleaks, Dependabot). Helm/Kubernetes, SBOM and image scanning DEFERRED (compose is enough for a portfolio demo) |
| M10 Scale, chaos, benchmark breadth | DEFERRED | Load and chaos campaigns and 25 scenario types are cut; public-benchmark evaluation (RCAEval) is stretch S2 |
| M11 Release candidate | DEFERRED | Replaced by the portfolio release `v1.0.0`; Council E superseded by councils P1/P2 |

---

## M0 — Audit, decisions, research preservation · `DONE`

| Acceptance criterion | Status |
|---|---|
| `docs/production-readiness-audit.md` with baseline, 12-way separation, required headings, confirmed defects | DONE (`67cf1f3`) |
| Reproducible defect script `scripts/audit/repro_research_defects.py` | DONE (7 confirmed, 1 latent; concurrency loss confirmed by security review) |
| Independent adversarial security review merged | DONE (Appendix A) |
| Research tag `v1.1-research-freeze`; branch `production/platform` | DONE |
| Research packaging fixed (wheel ships `src`), scripts lint-clean, run record archived, OTel fixture pinned | DONE (`44ad5f8`) |
| Held-out set frozen before pipeline code | DONE (`cf07159`) |
| Council A run; architecture, ADR-006..015, roadmap written | DONE |
| Councils B (RCA) and C (security) run; ADRs updated (008/009/011/012/014 amended, 016/017 added); security model written | DONE |
| Held-out expected answers written and hashed before pipeline code | DONE (owner sign-off pending) |
| License: README claims MIT but no LICENSE file exists | DONE (2026-11-09) — owner chose MIT; `LICENSE` © 2026 Joshua Herbert Japheth; `backend/pyproject.toml` `license = { text = "MIT" }` |

## M1 — Pure RCA core, scenario format, benchmark gate · `DONE` (LLM-free part; see notes)
Depends on: Council B (done).

| Acceptance criterion | Status |
|---|---|
| `backend/` package `aegis` (distribution `aegis-platform`) with locked dependencies, ruff format + lint, mypy strict, pytest | DONE |
| Import-boundary test: no `aegis` module imports `src` | DONE (`tests/test_import_boundary.py`) |
| Versioned snapshot/request/analysis contract; `analyze()` performs no I/O and reads no clock | DONE (`tests/core/test_purity.py`, determinism test) |
| Regression tests for every confirmed research defect against the core contract | DONE (`tests/core/test_research_defect_regressions.py`) |
| Stages incl. coverage, topology, signals, baseline anomaly, error origin, latency attribution, log signatures, change correlation, decision list, tri-state checks, verdict with support level | DONE (`docs/rca.md`) |
| Redaction functions with tests | **MOVED to M2/M4** (redaction is applied at collection time, which is built there) |
| Scenario format with structured ground truth; healthy, 5xx, DB latency, downstream outage, deployment regression, insufficient evidence, red herring, prompt injection, partial outage (+ masked fallback, saturation, multi-fault, intermittent, flag ± change feed) | DONE (15 scenarios) |
| Structured evaluator alongside the research evaluator | DONE (`aegis.benchmark.evaluate`) |
| Benchmark runner + CI gate; held-out set evaluated and reported, never tuned on | DONE — synthetic 15/15 (regression gate, not an accuracy estimate); held-out run 1 (clean): 0 confident-wrong; run 2 reported as post-hoc |

## M2 — Persistence, jobs, API, auth, tenancy, audit, ingestion (the slice backbone) · `DONE`
Depends on: M1, Council C.

| Acceptance criterion | Status |
|---|---|
| PostgreSQL schema via Alembic; owner vs `aegis_app` roles; RLS + `FORCE` on every tenant table; claim and auth-lookup `SECURITY DEFINER` functions | DONE — migrations 0001–0004, catalog tests, downgrade round trip (`docs/database.md`) |
| Cross-tenant leak tests for every repository and route | DONE for the M2 surface — SQL, pooled-connection, repository (queue/worker) and 16 API routes; later resources add their own |
| Job queue: transactional enqueue, `SKIP LOCKED` claim with per-org cap, leases/heartbeats, bounded jittered retries, cancellation, idempotency, one active investigation per incident; tests for duplicate jobs, worker crash (lease expiry), cancellation | DONE (`tests/db/test_queue_and_worker.py`, `test_worker_process.py`) |
| Worker runs snapshot collection (replay source first) → core → persists graph and verdict → events | DONE for the scenario (replay) source; live collection is M4 |
| REST `/api/v1` (incidents, alerts, investigations, evidence, events, change-events, audit, me, organizations, users/memberships, api-keys) with pagination, filtering, sorting, stable error schema, request IDs, OpenAPI | DONE (`docs/api.md`) |
| SSE investigation stream (event-table polling) + REST polling fallback | DONE — verified over a real TCP socket |
| OIDC BFF login with sessions, CSRF defence, API keys, RBAC matrix enforced server-side, tested per route | DONE — code + PKCE flow (`tests/api/test_oidc.py`), live against Dex (`test_oidc_dex_live.py`) |
| Alertmanager + generic webhook ingestion with ingest-scoped keys, fingerprint dedup, grouping window, auto-investigation once per incident with cooldown | DONE (auto-investigation once per incident; deferral when the queue is full) |
| Hash-chained append-only audit (no UPDATE/DELETE grants), verification endpoint | DONE (`/api/v1/audit/verify`, `aegis audit verify`) |
| Configuration system with environment profiles and startup validation that rejects unsafe production settings | DONE (profile defaults to production; API and worker refuse unsafe settings) |

## M3 — LLM adjudication · `PARTIAL`
Depends on: M1, M2. Details: [`ai.md`](ai.md).

| Acceptance criterion | Status |
|---|---|
| Provider interface with capability metadata; Ollama, OpenAI-compatible, Anthropic adapters; contract tests with recorded payloads; live test against local Ollama | PARTIAL — three adapters with request/response shape tests (constructed payloads); live Ollama test passes (`tests/llm/test_ollama_live.py`); OpenAI/Anthropic live calls **BLOCKED** (no credentials in this environment) |
| Schema-constrained output; pure validator enforcing citation validity, ceiling, classification ordering, candidate membership; one repair attempt; deterministic fallback | DONE (`tests/llm/test_validate.py`, `tests/db/test_ai_adjudication.py`) |
| Untrusted-data channel in prompts; injection scenarios in the benchmark | PARTIAL — channel done and tested; the injection scenario was run against a live model (rejected by the validator); LLM benchmark runs are M10 |
| Global LLM slot pool, per-call/total timeouts, retry policy, circuit breaker, token and cost accounting, per-org provider allowlist (no silent local→cloud fallback) | PARTIAL — all but cost accounting (tokens are accounted; no price table) |
| Failure scenarios: malformed JSON, empty output, timeout, provider down, hallucinated evidence/service, contradictory output, context overflow | PARTIAL — all except an explicit "contradictory claims" case; context overflow handled by sizing the prompt to the context window (found live) |
| Benchmark reports deterministic-only vs deterministic+LLM with repeated runs | PLANNED (M10) |

## M4 — Live telemetry integrations · `PARTIAL`
Depends on: M1, M2. Details: [`integrations.md`](integrations.md).

| Acceptance criterion | Status |
|---|---|
| SSRF-safe egress client (pinned DNS, deny ranges, allowlist, no redirects, size caps) with tests | DONE (`tests/integrations/test_egress.py`, `tests/api/test_integrations.py`) |
| Prometheus (RED signals via configurable metric mapping), Loki and OpenSearch logs, Jaeger and Tempo traces with span status, Kubernetes events/rollouts, change events; all queries built from validated parameters, never from free text | PARTIAL — Prometheus, Loki, OpenSearch, Jaeger, change events done; Tempo and Kubernetes PLANNED |
| Contract tests from recorded real responses for each backend; live tests against the demo stack | DONE for Prometheus, Jaeger, Loki (recorded from the demo stack; live test CONFIRMED the injected flag fault); OpenSearch fixture constructed (no OpenSearch in the demo stack) |
| Integration configuration API with encrypted credentials and connectivity test | DONE |

## M5 — Web application · `PLANNED`
Depends on: M2 (M3/M4 for full content).

| Acceptance criterion |
|---|
| React + TypeScript app: login, dashboard, incident list with filters, incident detail (summary, timeline, impact, topology, evidence, hypotheses/candidates, checks, diagnosis with clickable citations, uncertainty, remediation, audit), live investigation view, evidence explorer, settings (integrations, LLM, API keys, users/roles, policies, retention), system health, audit log |
| Visual distinction between observation, inference, hypothesis, verified finding, uncertainty |
| Unit/component tests; accessibility checks; Playwright E2E against the compose stack |

## M6 — Remediation and notifications · `PARTIAL`
Depends on: M2, M4. Details: [`remediation.md`](remediation.md).

| Acceptance criterion | Status |
|---|---|
| Proposal → approval (role + two-person rule) → execution → verification → rollback via inverse; idempotent execution; full audit | DONE in code and tests (`tests/api/test_remediation.py`); live verification against the demo stack pending |
| One real executor (flag service) used by the demo; execution disabled per org by default | DONE |
| Slack, PagerDuty and generic webhook notification adapters with contract tests (live delivery only if credentials are provided) | PLANNED |

## M7 — Containers, compose stack, reproducible demo · `PLANNED`
Depends on: M2–M6, Council D.

| Acceptance criterion |
|---|
| Images: api/worker, frontend, migrate; non-root, pinned bases, health checks |
| Compose: Postgres, Dex, Aegis, demo services, OTel Collector, Prometheus, Alertmanager, Jaeger, Loki, flag service, load generator |
| Documented clean-machine demo: healthy → fault → alert → incident → investigation → cited diagnosis → approval → remediation → recovery → audit, with no hidden manual steps |

## M8 — Self-observability, retention, MCP, topology · `PLANNED`

| Acceptance criterion |
|---|
| JSON logs with correlation IDs, Prometheus metrics, OTel traces, `/healthz` and `/readyz` |
| Retention policies and sweep, tested |
| Production MCP client: persistent sessions, streamable HTTP for remote servers, fail-closed allowlist, bounds, provenance namespacing; hostile-server tests. Tenant-scoped, API-key-authenticated MCP server exposing read-only incident/evidence tools |
| Topology service graph in API and UI |

## M9 — Kubernetes packaging and CI/CD · `PLANNED`
Depends on: M7, Council D.

| Acceptance criterion |
|---|
| Helm chart (probes, resources, HPA, NetworkPolicy, Ingress/TLS, Secrets, migration job); `helm lint`, `helm template` and kubeconform pass; any cluster run is recorded, otherwise Kubernetes readiness is not claimed |
| GitHub Actions: format, lint, type check, backend/research/frontend tests, migrations, benchmark gate, builds, gitleaks, pip-audit, npm audit, SBOM, image scan, compose/Helm validation; every step also runnable locally via the same scripts |

## M10 — Scale, chaos, benchmark breadth · `PLANNED`

| Acceptance criterion |
|---|
| 25 scenario types with repeated runs; research questions answered with stated sample sizes |
| Concurrency tests at 1/5/10/25 simultaneous investigations with measured API latency, queue latency, worker utilisation, DB contention |
| Failure injection: backend timeout/unavailable/malformed/slow/partial, LLM timeout/malformed, DB loss, worker crash, duplicate job, cancellation, overload, network interruption |
| Profiling-driven fixes; documented performance budgets |

## M11 — Release candidate · `PLANNED`

| Acceptance criterion |
|---|
| Council E war-room; findings fixed or recorded as known limitations |
| `docs/final-production-audit.md`; full verification suite re-run; `docs/production-completion-report.md` with verified / partially verified / not verified |
| All documents in prompt §41 exist and match reality |
