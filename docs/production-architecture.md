# Aegis — Production Architecture

**Status:** decided design, 2026-10-01. Shaped by the [production readiness audit](production-readiness-audit.md), [Council A](councils/council-A-architecture.md) (architecture), [Council B](councils/council-B-rca.md) (RCA) and [Council C](councils/council-C-security.md) (security). What is *built and verified* is tracked separately in [`production-readiness.md`](production-readiness.md); this document describes intent and must not be read as a claim that a component exists.

---

## 1. Principles

1. **Trust before plumbing.** The one thing Aegis offers that an alert does not is a diagnosis an operator can believe. Every architectural choice is judged first by whether it keeps conclusions traceable to evidence and honest about uncertainty.
2. **Deterministic first, AI second.** Deterministic analysis computes anomalies, candidates, tri-state verification checks and an ordinal *support level*. The LLM (off by default, enabled per organization) narrates within that level; it can lower support, never raise it. The product is fully useful with the LLM switched off.
8. **Inputs before reasoning.** The research engine's failures were missing or corrupted inputs (no change feed, wrong window, discarded span status). Coverage — including "what was not checked" — and the change ledger are first-class outputs.
3. **One RCA core for live, replay and research.** The core is a pure function over an explicit telemetry snapshot and window. Production, the demo, the scenario benchmark and research experiments call the same code.
4. **Fail closed.** Tenancy (RLS), authorization (server-side RBAC), egress (allowlist), tools (allowlist), remediation (disabled until enabled per organization) and model output (rejected unless valid) all default to denial.
5. **Telemetry is hostile data.** Log bodies, span attributes, metric labels, alert annotations and tool output are untrusted. They are redacted, bounded, and presented to the model only inside a labelled data channel.
6. **One stateful dependency.** PostgreSQL holds data, jobs, sessions, the audit chain and coordination. Nothing else is introduced without a measured need.
7. **Research is preserved, not inherited.** The research engine under `src/` stays frozen, tagged and tested; the product is a new package that never imports it.

## 2. System context

```
   Alertmanager / generic webhook ──┐                         ┌── Prometheus (metrics)
   Change events (deploy/flag/cfg) ─┤                         ├── Loki / OpenSearch (logs)
   Operators (browser) ─────────────┤      ┌────────────┐     ├── Jaeger / Tempo (traces)
   Machines (API keys) ─────────────┼─────►│   AEGIS    │────►├── Kubernetes API (events, rollouts)
                                    │      └────────────┘     ├── LLM providers (Ollama, OpenAI-compatible, Anthropic)
   OIDC identity provider ◄─────────┘            │            ├── MCP tool servers (read-only allowlist)
                                                 └──────────►└── Slack / PagerDuty / webhooks (notifications)
                                                               Remediation targets (one real executor: demo flag service)
```

## 3. Components

| Component | Runtime | Responsibility |
|---|---|---|
| **frontend** | static SPA (React + TypeScript + Vite) behind nginx | Dashboard, incidents, investigation live view, evidence explorer, RCA view, remediation approvals, topology, settings, audit. Talks only to `/api/v1` and `/auth`. |
| **api** | FastAPI (uvicorn), stateless, N replicas | AuthN (OIDC BFF sessions, API keys), RBAC, tenant scoping, REST resources, webhook ingestion with dedup/grouping, SSE streams, health/readiness, OpenAPI. Never runs an investigation inline. |
| **worker** | async Python process, N replicas | Claims jobs (`SKIP LOCKED`), collects telemetry snapshots, runs the RCA core, adjudicates with the LLM under a global slot pool, proposes remediation, executes *approved* remediation, runs verification, retention sweeps, notifications. |
| **migrate** | one-shot container | Alembic migrations as the owning role; creates the restricted application role and RLS policies. |
| **postgres** | PostgreSQL 16 | All state. |
| **demo stack** (dev/demo only) | compose profile | Small instrumented services → OTel Collector → Prometheus / Jaeger / Loki, Alertmanager, a flag service with a reversible fault; Dex as identity provider. |

## 4. Code layout

```
src/ tests/ scripts/ eval/ config/     research layer (frozen at v1.1-research-freeze; still tested)
backend/aegis/
  core/            PURE: models, window, analysis (baseline, anomaly, traces, logs, topology,
                   changes), candidates, verification, verdict, llm_contract, prompting, redaction
  integrations/    telemetry/ llm/ notify/ mcp/ remediation executors; egress.py (SSRF-safe HTTP)
  collection/      live snapshot collection (I/O) → core.TelemetrySnapshot
  db/              SQLAlchemy models, repositories (tenant-scoped), Alembic migrations
  jobs/            queue, worker loop, LLM slot pool, retry/lease logic
  api/             FastAPI app, routers, error schema, pagination, SSE
  auth/            OIDC BFF, sessions, API keys, RBAC
  ingest/          Alertmanager + generic webhook normalisation, fingerprinting, grouping
  remediation/     catalogue, policy, approval workflow, verification
  audit/           hash-chained append-only audit log
  observability/   JSON logging, metrics, tracing
  benchmark/       scenario format, runner, structured evaluator (research may import this)
backend/tests/     unit, contract, integration (Postgres), security, chaos
frontend/          React app
deploy/            Dockerfiles, compose, Helm chart
demo/              demo services and observability stack configuration
eval/scenarios/    product benchmark scenarios (synthetic, labelled as such)
eval/heldout/      frozen held-out set (real research captures)
```

**Import rule:** `backend/aegis` must not import `src` (enforced by a test). Research code may import `aegis.benchmark`/`aegis.core` to evaluate the product.

## 5. The RCA core (pure)

```
analyze(snapshot: TelemetrySnapshot, request: AnalysisRequest) -> Analysis
```

* **Inputs.** `AnalysisRequest` carries the incident window, the baseline window, focus services (from the alert), and budgets. `TelemetrySnapshot` carries metric series, log records, spans *with status and parent links*, change events, configured topology edges, per-item provenance (provider, query, collected-at), and per-provider collection outcomes (ok / partial / failed / not configured). No clock, network or database access occurs inside `analyze`.
* **Evidence identity.** Evidence IDs are content-derived and stable for a given snapshot (replay-stable), and persisted under the composite key `(org_id, incident_id, evidence_id)`. The same trace observed by two incidents is two rows; nothing can be moved between incidents (fixes DATA-01).
* **Stages.** Window, baseline validity and coverage → topology (configured edges ∪ edges derived from cross-service parent/child spans) and per-edge call-count diff (baseline vs incident) → per-service signals (request rate, error ratio, latency percentiles, saturation; from metrics, or derived from spans) → baseline-vs-incident anomaly detection (ratio + robust z-score with a MAD floor and minimum counts, onset time) → trace error-origin analysis (deepest failing span per trace, only where span status exists) → log signature counting (template masking; a new signature needs a minimum count from more than one instance) → change correlation with scope match → candidates ranked by an **ordered decision list** (one sentence per rule, all features logged) → **tri-state** verification checks per candidate (temporal precedence with a tolerance band, propagation, isolation, change-scope match, modality agreement, saturation) → deterministic verdict, mechanism lookup, and the next check.
* **Verdict.** Classification (`CONFIRMED`, `PLAUSIBLE`, `INSUFFICIENT`, `NO_ANOMALY`), leading candidate(s) (several when independent faults co-occur), an ordinal **support level** (`strong`/`moderate`/`weak`/`none`), supporting and contradicting evidence IDs, coverage gaps, blast radius, mechanism class, and the next check. Rules (ADR-009 amendment) are explicit and unit-tested: `CONFIRMED` needs ≥2 modalities, temporal precedence outside the tolerance band, propagation on real span status, isolation or a scope-matched change, a valid baseline and no failed check — change alignment never substitutes for propagation; a missing change feed caps the result at `PLAUSIBLE`; a candidate whose downstream dependency on the failing path is unevaluated cannot be named root cause. Display wording: "Strongly supported", "Likely cause", "Not enough evidence — next check: …", "Nothing found in what was checked" (never "no anomaly" for a declared incident).
* **Output graph.** Evidence items, relationships (`metric→service`, `log→trace`, `span→span`, `span→service`, `change→service`, `evidence→candidate`), candidates and checks. This is what the UI renders and what the LLM sees.

## 6. LLM adjudication

* Off by default; enabled per organization by an admin when an allowed provider exists. Runs after the deterministic verdict has been persisted and streamed (operators see the deterministic result within seconds). No tool loop. A later AI downgrade is shown as a visible notice.
* Input: a bounded, redacted projection of the graph. Instructions travel in the system role; telemetry travels only inside a delimited, explicitly labelled *untrusted data* block, with control characters stripped and per-field length caps.
* Output: schema-constrained JSON (Ollama `format`, OpenAI `response_format: json_schema`, Anthropic forced tool use), containing a chosen candidate ID or `none`, a mechanism explanation as a list of claims (each claim typed `observation` or `inference` and carrying evidence IDs), contributing factors, contradicting evidence, missing information and confidence.
* Validation (pure): unknown evidence IDs, unknown services or candidates, confidence above the ceiling, a classification stronger than the deterministic one, or uncited claims → the claim is rejected or the assessment is rejected as a whole. Choosing a non-leading candidate caps classification at `PLAUSIBLE` and is shown to the operator as a disagreement. One repair retry with the validation errors; then the deterministic verdict stands with "AI assessment unavailable/invalid".
* Providers: Ollama, OpenAI-compatible, Anthropic behind one interface with capability metadata (structured output mode, context window, tool support). Per-provider circuit breaker, timeouts, retry only on retry-safe failures, token and cost accounting per call. Per-organization allowlist of providers; **no fallback from a local provider to a cloud provider unless that organization allows cloud providers**.
* Concurrency: a global LLM slot pool (lease rows in PostgreSQL) bounds concurrent model calls across all workers.
* Chain-of-thought: model "thinking" fields are discarded, never stored or displayed. The product shows structured claims, citations, checks and confidence.

## 7. Jobs, concurrency and live updates

* `investigation_jobs` rows are created in the same transaction as the incident/investigation (no dual write). Workers claim through a narrow `SECURITY DEFINER` function that returns `(job_id, org_id)` using `FOR UPDATE SKIP LOCKED`, honouring a per-organization running-job cap (fairness) and priority/age ordering.
* Leases with heartbeats; an expired lease makes the job claimable again. Attempts are bounded; retry uses jittered exponential backoff and applies only to retry-safe failures (provider timeouts, worker loss). Investigations are resumable at stage boundaries because each stage persists its output.
* Cancellation: an operator sets `cancel_requested`; the worker checks between stages and before each external call and transitions to `CANCELLED`.
* Idempotency: ingestion and investigation start accept idempotency keys; a unique constraint guarantees one active investigation per incident.
* Backpressure: when an organization's queue exceeds its limit, new automatic investigations are deferred (recorded, not dropped) and the API reports it.
* Every state transition writes an `investigation_events` row (state, timestamp, actor, trigger, tool/model, evidence refs, error, duration). The SSE endpoint polls this table (1 s) per subscribed investigation; a polling REST endpoint serves the same data for clients without SSE.

## 8. Data model (summary)

Tenant-owned tables carry `org_id` and are protected by RLS: `incidents`, `alerts`, `investigations`, `investigation_jobs`, `investigation_events`, `snapshots`, `evidence`, `evidence_relations`, `candidates`, `verification_checks`, `diagnoses`, `diagnosis_claims`, `llm_calls`, `remediation_proposals`, `remediation_approvals`, `remediation_executions`, `change_events`, `integrations` (credentials encrypted), `api_keys`, `memberships`, `audit_events`, `retention_policies`. Global tables: `organizations`, `users`, `sessions`, `llm_slots`. Full schema: [`database.md`](database.md) (written with migration 1).

## 9. Security architecture

Authoritative detail: [`security-model.md`](security-model.md). In brief:

* **Identity.** OIDC authorization code + PKCE handled by the backend; server-side sessions referenced by an HttpOnly, Secure, SameSite=Lax cookie; CSRF defence by Origin check + required custom header on state-changing requests. Hashed, prefixed, scoped API keys for machines; webhook ingestion requires an ingest-scoped key and the **key determines the organization**.
* **Authorization.** Roles `admin`, `investigator`, `reviewer`, `viewer` per organization membership; permissions checked server-side on every route; remediation approval requires `reviewer` and, by default, a different person from the proposer.
* **Tenancy.** RLS with `FORCE ROW LEVEL SECURITY`; the application connects as a non-owner role without `BYPASSRLS`; `SET LOCAL aegis.org_id` per transaction; cross-tenant leak tests for every repository and every route.
* **Egress.** Tenants never type URLs: the operator's connector registry binds exact endpoints to organizations, credentials are bound to (organization, connector), and one egress client pins DNS, verifies TLS against the original hostname, always denies metadata/link-local and Aegis's own infrastructure, disables redirects and caps responses (ADR-016).
* **Data protection.** Redaction of secrets/PII before storage and before prompting; provider credentials encrypted at rest (key from environment/secret manager, rotation via key IDs); configurable retention; safe error messages.
* **Audit.** Append-only `audit_events`: the application role has no UPDATE/DELETE grants; each organization's events form a SHA-256 hash chain verifiable by an endpoint and a CLI.
* **Tools/MCP.** Fail-closed allowlists, schema validation, argument and result size bounds, timeouts, persistent sessions, provenance re-stamped by Aegis (remote IDs are never trusted as keys), mutating tools unreachable outside the remediation workflow.
* **Remediation.** Disabled per organization by default; one real, reversible action type implemented end to end; every execution requires an approved proposal, is idempotent by proposal ID, and is followed by deterministic verification.

## 10. Remediation lifecycle

`recommended → proposed → approved | rejected → executing → executed | failed → verified | verification_failed → (rolled_back)`

A proposal records action type, target, parameters, risk, expected impact, required permission, reversibility (and its inverse action), supporting evidence IDs, and whether approval is required. Rollback is the inverse action going through the same approval path. Post-action verification re-collects the affected service's signals and compares them with the incident window.

## 11. Ingestion and grouping

Generic webhook and Alertmanager payloads are normalised to `Alert` (source, fingerprint, status, severity, service, environment, labels, annotations, starts/ends). Alerts with the same fingerprint update one alert row. Alerts are attached to an open incident when they share organization + service + environment within a grouping window; otherwise a new incident is created. One automatic investigation per incident, with a cooldown before automatic re-investigation. Change events (deploy, config, feature flag, rollout) arrive through `/api/v1/change-events` (ingest-scoped API key) and from the Kubernetes integration.

## 12. Self-observability

JSON logs with `request_id`, `org_id`, `investigation_id`, `job_id` from context variables; Prometheus metrics (HTTP latency/throughput/errors, investigation duration and outcome, queue depth and age, active investigations, provider and model latency/errors, tokens, tool calls, evidence counts, DB latency, remediation executions/failures); OpenTelemetry traces exported via OTLP when configured; `/healthz` (liveness: process up) and `/readyz` (readiness: database reachable, migrations at head).

## 13. Deployment

Container images: `api` and `worker` (same backend image, different command), `frontend`, `migrate`. Docker Compose provides the developer and demo stack. A Helm chart packages api, worker, frontend and migration job with probes, resources, HPA for api/worker, NetworkPolicy, Ingress/TLS, Secrets; PostgreSQL is an external dependency. Verification claims for Kubernetes are limited to what is actually executed (chart rendering, schema validation, and any cluster runs that are performed and recorded). Details: [`deployment.md`](deployment.md).

## 14. Decision records

| ADR | Decision |
|---|---|
| [ADR-006](decisions/ADR-006-research-preservation.md) | Research layer frozen in place; compatibility strategy |
| [ADR-007](decisions/ADR-007-product-package-boundary.md) | New `backend/aegis` package; one-way import boundary |
| [ADR-008](decisions/ADR-008-pure-rca-core.md) | Pure RCA core over explicit snapshots and windows |
| [ADR-009](decisions/ADR-009-llm-authority.md) | LLM authority bounded by a deterministic confidence ceiling; structured output |
| [ADR-010](decisions/ADR-010-postgres-jobs.md) | PostgreSQL job queue, LLM slot pool, fairness, SSE via event polling |
| [ADR-011](decisions/ADR-011-authentication.md) | OIDC backend-for-frontend sessions and hashed API keys |
| [ADR-012](decisions/ADR-012-tenancy-rls.md) | Tenant isolation with RLS plus application scoping |
| [ADR-013](decisions/ADR-013-change-events.md) | Change events as first-class evidence |
| [ADR-014](decisions/ADR-014-remediation.md) | Human-gated remediation with one real reversible action |
| [ADR-015](decisions/ADR-015-data-protection.md) | Redaction, egress policy, credential encryption, retention |
| [ADR-016](decisions/ADR-016-connector-registry.md) | Operator-owned connector registry for all egress |
| [ADR-017](decisions/ADR-017-mcp-policy.md) | MCP off by default, read-only, pinned |
