# Aegis — Production Roadmap

**Status legend:** `DONE` (acceptance criteria met and verified as stated) · `IN PROGRESS` · `PLANNED` · `PARTIAL` (some criteria met; the gap is stated) · `BLOCKED` (external dependency; the workaround is stated).
Status is updated in the same commit that changes it. "Verified" means a command was run and its output recorded in `production-readiness.md`; nothing else counts.

Build order follows Council A: core-first vertical slice → wrap the slice with persistence, jobs, API and UI → LLM → breadth. Councils B (RCA) and C (security) run before the core contract and the first migration are written; Council D before deployment work; Council E on the release candidate.

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
| License: README claims MIT but no LICENSE file exists | **BLOCKED on owner** — the copyright holder and licence choice are the owner's decision; flagged in the completion report |

## M1 — Pure RCA core, scenario format, benchmark gate · `IN PROGRESS`
Depends on: Council B.

| Acceptance criterion |
|---|
| `backend/` package `aegis` (distribution `aegis-platform`) with locked dependencies, ruff format + lint, mypy strict, pytest |
| Import-boundary test: no `aegis` module imports `src` |
| Versioned `TelemetrySnapshot` / `AnalysisRequest` / `Analysis` contract; `analyze()` performs no I/O and reads no clock (test asserts it) |
| Regression tests for every confirmed research defect expressed against the core contract (evidence identity, window explicitness, span status, no fabricated citations, no keyword classification, bounded inputs) |
| Stages: coverage, topology from spans, service signals, baseline anomaly, trace error origin, log signatures, change correlation, candidates, verification checks, verdict with confidence ceiling |
| Redaction functions with tests |
| Scenario format (synthetic, labelled) with structured ground truth; first scenarios: healthy baseline, HTTP 5xx spike, DB latency, downstream outage, deployment regression, insufficient evidence, red herring, prompt injection in logs, partial telemetry outage |
| Structured evaluator (component, mechanism class, classification, citation validity, abstention correctness, calibration) alongside the research evaluator |
| Benchmark runner + CI gate (deterministic mode); held-out set evaluated and reported, never tuned on |

## M2 — Persistence, jobs, API, auth, tenancy, audit, ingestion (the slice backbone) · `PLANNED`
Depends on: M1, Council C.

| Acceptance criterion |
|---|
| PostgreSQL schema via Alembic; owner vs `aegis_app` roles; RLS + `FORCE` on every tenant table; claim and auth-lookup `SECURITY DEFINER` functions |
| Cross-tenant leak tests for every repository and route |
| Job queue: transactional enqueue, `SKIP LOCKED` claim with per-org cap, leases/heartbeats, bounded jittered retries, cancellation, idempotency, one active investigation per incident; tests for duplicate jobs, worker crash (lease expiry), cancellation |
| Worker runs snapshot collection (replay source first) → core → persists graph and verdict → events |
| REST `/api/v1` (incidents, alerts, investigations, evidence, events, change-events, audit, me, organizations, users/memberships, api-keys) with pagination, filtering, sorting, stable error schema, request IDs, OpenAPI |
| SSE investigation stream (event-table polling) + REST polling fallback |
| OIDC BFF login with sessions, CSRF defence, API keys, RBAC matrix enforced server-side, tested per route |
| Alertmanager + generic webhook ingestion with ingest-scoped keys, fingerprint dedup, grouping window, auto-investigation once per incident with cooldown |
| Hash-chained append-only audit (no UPDATE/DELETE grants), verification endpoint |
| Configuration system with environment profiles and startup validation that rejects unsafe production settings |

## M3 — LLM adjudication · `PLANNED`
Depends on: M1, M2.

| Acceptance criterion |
|---|
| Provider interface with capability metadata; Ollama, OpenAI-compatible, Anthropic adapters; contract tests with recorded payloads; live test against local Ollama |
| Schema-constrained output; pure validator enforcing citation validity, ceiling, classification ordering, candidate membership; one repair attempt; deterministic fallback |
| Untrusted-data channel in prompts; injection scenarios in the benchmark |
| Global LLM slot pool, per-call/total timeouts, retry policy, circuit breaker, token and cost accounting, per-org provider allowlist (no silent local→cloud fallback) |
| Failure scenarios: malformed JSON, empty output, timeout, provider down, hallucinated evidence/service, contradictory output, context overflow |
| Benchmark reports deterministic-only vs deterministic+LLM with repeated runs |

## M4 — Live telemetry integrations · `PLANNED`
Depends on: M1, M2.

| Acceptance criterion |
|---|
| SSRF-safe egress client (pinned DNS, deny ranges, allowlist, no redirects, size caps) with tests |
| Prometheus (RED signals via configurable metric mapping), Loki and OpenSearch logs, Jaeger and Tempo traces with span status, Kubernetes events/rollouts, change events; all queries built from validated parameters, never from free text |
| Contract tests from recorded real responses for each backend; live tests against the demo stack |
| Integration configuration API with encrypted credentials and connectivity test |

## M5 — Web application · `PLANNED`
Depends on: M2 (M3/M4 for full content).

| Acceptance criterion |
|---|
| React + TypeScript app: login, dashboard, incident list with filters, incident detail (summary, timeline, impact, topology, evidence, hypotheses/candidates, checks, diagnosis with clickable citations, uncertainty, remediation, audit), live investigation view, evidence explorer, settings (integrations, LLM, API keys, users/roles, policies, retention), system health, audit log |
| Visual distinction between observation, inference, hypothesis, verified finding, uncertainty |
| Unit/component tests; accessibility checks; Playwright E2E against the compose stack |

## M6 — Remediation and notifications · `PLANNED`
Depends on: M2, M4.

| Acceptance criterion |
|---|
| Proposal → approval (role + two-person rule) → execution → verification → rollback via inverse; idempotent execution; full audit |
| One real executor (flag service) used by the demo; execution disabled per org by default |
| Slack, PagerDuty and generic webhook notification adapters with contract tests (live delivery only if credentials are provided) |

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
