# Council A — Production architecture

**Date:** 2026-10-01 · **Protocol:** installed `llm-council` skill (5 independent advisors in parallel → 5 anonymized peer reviews → chairman synthesis) · **Advisors and reviewers:** isolated sub-agents that saw only the framed question below.

## Framed question

DECISION: What architecture should Aegis adopt to transform an existing research engine into a production observability / AI incident-investigation platform while preserving its research capabilities?

WHAT AEGIS IS TODAY (verified by a code audit, 2026-10-01):
- ~4,300 lines of Python 3.13 research prototype. Pydantic domain models (Incident, Evidence, Hypothesis), abstract provider interfaces, sync httpx adapters for Prometheus, OpenSearch, Jaeger and Ollama (local LLM, qwen3.5:4b on CPU), a 640-line synchronous state-machine engine (collect evidence -> hypotheses -> verify -> conclude -> recommend), regex parsing of free-text LLM output ("DIAGNOSIS: ...", "CLASSIFICATION: ..."), SQLite persistence, JSON snapshot replay, a keyword-based RCA evaluator (kappa 0.778 vs one human on 12 cases), an MCP server/client. 165 tests, mypy strict clean. CLI only. No API, UI, auth, users, tenants, job queue, containers, CI.
- Research value worth preserving: deterministic replay, evaluator + human annotation benchmark, fault-injection harness against the OpenTelemetry Demo, error-focused querying, pre-LLM summarization, model comparison, MCP experiment, tagged and documented (v1.1-research-freeze).
- Confirmed defects in the research engine (reproduced): evidence keyed by global IDs with INSERT OR REPLACE, so two incidents seeing the same trace silently move the evidence row between incidents; when the model cites no evidence the engine attaches keyword-chosen evidence IDs as "supporting"; "payment-api is unhealthy" is classified NO_ANOMALY by substring match; all queries use now()-15min instead of the incident window; Jaeger normalization drops span status so error spans are invisible; untrusted log text is concatenated into the USER role of prompts; MCP client allowlist fails open when empty; LLM failure => investigation FAILED with nothing useful produced.
- Research's own finding: small local models localize the failing service but do not deduce mechanisms; error-focused, summarized evidence helps; open-ended tool loops are slow on CPU (2-6 min per investigation).

TARGET PRODUCT (from the owner's requirements): multi-user, multi-tenant web product. Alert/webhook (Alertmanager) -> incident (dedup/grouping) -> durable async investigation (cancel, retry, resume, budgets) -> deterministic telemetry correlation + LLM hypothesis/diagnosis with mandatory evidence citations and abstention (CONFIRMED/PLAUSIBLE/INSUFFICIENT/NO_ANOMALY) -> human review -> human-approved remediation (read-only by default) -> post-action verification -> append-only audit. React/TypeScript UI with live progress, evidence explorer, topology, RCA view. OIDC auth, RBAC (admin/investigator/reviewer/viewer), API keys, tenant isolation tests. Providers: Prometheus, OpenSearch, Jaeger required; Loki/Tempo/Kubernetes desirable; LLMs: Ollama, OpenAI-compatible, Anthropic, with fallback, circuit breaking, token/cost budgets. Self-observability (OTel traces, Prometheus metrics, JSON logs, health/readiness). Docker Compose dev stack, Helm chart, CI with security scanning. Research mode must keep working and must be able to benchmark the PRODUCTION RCA pipeline on replayed scenarios (25 scenario types incl. red herrings, prompt injection in logs, provider failure, insufficient evidence).

CONSTRAINTS: one autonomous engineering agent builds it on a Windows 16 GB laptop (Docker Desktop available, no local Postgres/Redis binaries); the OpenTelemetry Demo (~6 GB RAM) cannot run alongside the product stack and a 9B model. Expected scale: one to a few organizations, tens of concurrent investigations, alert bursts of hundreds/minute, LLM calls taking 10-150 s each. Security must be designed in (telemetry is hostile input, endpoints are SSRF targets, MCP tools may be dangerous, remediation may be destructive). Nothing may be claimed as verified unless actually tested.

PROPOSED ARCHITECTURE (draft, to be pressure-tested):
1. Freeze research code in place (src/, tests/, scripts/, eval/); build the product as a NEW package `aegis` under backend/ that must not import research `src.*` (enforced by a test); port adapter/summarizer knowledge rather than import it.
2. FastAPI control plane (/api/v1, OpenAPI, request IDs, stable errors, cursor pagination).
3. PostgreSQL 16 + SQLAlchemy 2 + Alembic; organization_id on every tenant-owned row; repositories require a tenant scope.
4. Investigation jobs stored IN PostgreSQL, claimed with SELECT ... FOR UPDATE SKIP LOCKED, leases + heartbeats, bounded jittered retries, cancellation flag, idempotency keys. No Redis unless a measured need appears.
5. Separate async worker process runs a deterministic RCA pipeline (window -> topology from traces -> baseline vs incident anomaly -> change/deploy correlation -> candidate ranking -> verification checks) producing an evidence graph; the LLM adjudicates via JSON-schema structured output; validators reject unknown evidence IDs/services; system abstains rather than guesses; a deterministic summary is always produced even when the LLM is down.
6. Auth: backend-for-frontend OIDC (code + PKCE) with server-side sessions in HttpOnly cookies; hashed scoped API keys for machines.
7. React + TS + Vite SPA; Server-Sent Events for live progress with polling fallback.
8. Remediation: typed action catalog; proposal -> role-gated approval -> execution (disabled per org by default) -> verification -> rollback.
9. Demo: a lightweight REAL telemetry stack (3 tiny instrumented services -> OTel Collector -> Prometheus/Jaeger/Loki + a reversible fault flag) instead of the 6 GB OTel Demo, which stays an optional live fixture.

OPEN CHOICES TO DECIDE (give a position on the ones your lens cares about):
(a) Postgres SKIP LOCKED job queue vs Redis/Celery/arq/Temporal.
(b) BFF cookie sessions vs SPA-held OIDC tokens.
(c) PostgreSQL Row-Level Security vs application-level tenant scoping only (or both).
(d) New package alongside frozen research vs refactoring src/ into the product vs moving research into research/.
(e) SSE vs WebSockets for live updates; LISTEN/NOTIFY vs polling to feed them.
(f) Build order: thin end-to-end vertical slice first vs layer-by-layer.
(g) Anything in the draft that is over-engineered, under-engineered, or missing for the stated scale.

WHAT IS AT STAKE: The owner's definition of done fails if an independent audit finds an obviously unfinished subsystem, a security hole (tenant leak, prompt injection steering a diagnosis, SSRF, unapproved remediation), or a diagnosis that looks confident without evidence. Over-building infrastructure risks leaving the core RCA/product half-done; under-building risks a toy that cannot be operated.


## Advisor responses

ADVISOR RESPONSES (de-anonymized; in peer review they were labelled: Contrarian=B, First Principles=E, Expansionist=D, Outsider=A, Executor=C):

**The Contrarian:**

The fatal flaw is that the draft puts its care into infrastructure, while trust in the diagnosis is the vaguest part. That is the first thing the audit will test.

1. **The validator checks the wrong thing.** Rejecting unknown evidence IDs does not stop injected log text from steering the model to cite *real but irrelevant* IDs. Make the fix structural. The LLM may keep or lower the confidence that deterministic verification computed, but never raise it. CONFIRMED should require a passed deterministic check.

2. **Change/deploy correlation has no data source.** Kubernetes is only "desirable", and nothing ingests deploy events. This stage will ship empty, which is exactly the "obviously unfinished subsystem" the audit is looking for. Add a deploy-event webhook or cut the stage.

3. **The benchmark grades itself.** One agent writes the demo services, the 25 scenarios, the red herrings and the pipeline. It then scores them with a keyword evaluator checked against one human on 12 cases. Freeze a held-out scenario set (OTel Demo replays) before any pipeline code exists.

4. **The worker is where tenants leak.** A SKIP LOCKED worker claims jobs across all organizations, so it runs unscoped. Use RLS (database-enforced row filtering by organization), and have the worker `SET LOCAL` the org from the claimed job before any other query. Provider URLs that tenants configure and a shared worker fetches are an SSRF hole. Resolve each hostname once and connect to that fixed IP, and block private and cloud-metadata address ranges by default.

5. **Remediation execution and rollback is the most dangerous subsystem and the most likely to be left half-built.** Ship proposal and approval with no executor at all. Code that is disabled by default can still be audited.

6. **Porting copies the bugs.** The reproduced defects live in the adapters being ported. Write a failing test for each one against the new package first.

7. **One CPU Ollama processes calls one at a time.** At 10–150 s per call, "tens of concurrent investigations" needs a global LLM concurrency limit and backpressure on queue depth.

Build the thin vertical slice first, but it must include the abstention and prompt-injection scenarios, not just the happy path.

**The First Principles Thinker:**

You're asking the wrong question. "What architecture" treats this as an infrastructure problem, but it's a trust problem. Done means "survives a hostile audit." The one thing Aegis offers that an alert doesn't is a diagnosis you can believe. OIDC, Helm and SSE are commodity plumbing, and all they add to the outcome is more for an auditor to attack.

Look at the defect list again. Evidence keyed by global IDs, citations the engine made up, a fixed now()-15min window, span status dropped, log text placed in the user role. That isn't a pile of separate bugs. Most of it is one bug: where evidence came from, and what the engine was given, are never stated explicitly. So the decision that matters most isn't Postgres vs Redis:

1. **Make the RCA core a pure function.** It takes a telemetry snapshot and an explicit time window, and returns an evidence graph and a verdict. It touches no database, no network and no clock. Live mode and replay mode call the same function, so "research benchmarks production" comes for free. Evidence is immutable and scoped to (org, incident).
2. **Take authority away from the LLM.** Your own research found that small models localize the failing service but don't work out the mechanism. So deterministic code computes the candidates and also a confidence ceiling based on how much evidence there is. The LLM may only pick from those candidate IDs, cite evidence that already exists, and lower confidence. It can never raise it. That one rule blocks diagnoses steered by injected log text and confident diagnoses without evidence. Validators that reject bad output one case at a time can't give that guarantee.
3. **Make the 25-scenario benchmark the CI gate from week one.** Give it structured ground truth (service and fault class, scored by exact match). Keyword scoring can't serve as a gate.

My positions on the open choices:
(a) Use the Postgres SKIP LOCKED queue. Dedup collapses alert bursts before they become jobs, and the LLM is the bottleneck, not the queue.
(c) Use both RLS and app-level scoping. One agent will eventually forget a WHERE clause, and with RLS that mistake fails closed.
(f) Build a vertical slice first. But the first slice is snapshot → core → verdict → benchmark, not webhook → UI.
(g) For remediation, ship exactly one real action: the demo's reversible fault flag, end to end with approval and verification. A generic executor that is "disabled by default" is exactly the unfinished subsystem an auditor will flag.

**The Expansionist:**

The draft treats the research engine as something to freeze and protect. That undersells it. Replay plus the evaluator plus human annotation is the strongest part of this project, and the product should be built around it.

**The bigger version:** every production investigation is stored as a replayable snapshot: the telemetry it saw, the evidence graph, the LLM calls, and the human reviewer's verdict. A reviewer's approval or rejection then becomes a labelled benchmark case. That gives you a loop where each incident improves the eval set. Over time the eval set improves prompts, ranking weights and eventually a small fine-tuned local model. The 25 synthetic scenarios are only the seed; real customer incidents are what make it valuable.

**Positions through that lens:**
- **(d)** Keep the new `aegis` package, but don't let the "no imports from research" rule turn into a wall. Replay should be a product feature: a "re-run with model X / prompt Y" button in the RCA view, plus a CI gate where any change to the pipeline or prompts must hold its benchmark score. That is your regression test and also your demo.
- **(f)** Build a vertical slice first, and include replay and scoring in that first slice. Benchmark from day one.
- **(a)** Postgres with SKIP LOCKED is fine. Make the append-only audit plus the evidence graph a proper event log, so replay comes almost for free.
- **Missing: shadow mode.** Run a candidate model or pipeline silently on live incidents and compare it with the primary. That makes switching between local and cloud LLMs an evidence-based decision.
- **Missing: Aegis as an MCP server.** Let coding agents and IDE assistants query incidents and evidence through MCP. It costs little, since the MCP experiment already exists, and it reaches users without the web UI.

**Undervalued positioning:** an engine that runs on a local model on CPU, abstains when evidence is missing, and always produces a deterministic summary fits air-gapped and regulated teams that can't send telemetry to OpenAI. Make the evidence citations and abstention the headline, not a footnote.

**The Outsider:**

Reading this cold, three things don't add up.

**1. The headline feature is the weakest part.** You're selling "AI incident investigation," but your own research says the AI can't investigate: small models point at the failing service but can't explain why, and each run takes 2-6 minutes. Item 5 already admits this, because the deterministic pipeline is the real product and the LLM only comments on it. So build it that way. The deterministic summary should appear within seconds of the alert, and the LLM's verdict can arrive later as an extra. An on-call engineer at 3am won't wait three minutes to be told "INSUFFICIENT."

**2. The throughput numbers don't work.** The draft promises "tens of concurrent investigations," each making several LLM calls at 10-150 seconds, all on one CPU-bound Ollama. In practice that means one investigation at a time and a queue that grows without limit. You need a global cap on LLM calls running at once and a way to refuse or delay new work when the queue gets too deep. You also need grouping strict enough to turn hundreds of alerts per minute into a handful of investigations. Grouping matters more here than which job queue you pick.

**3. "Freeze research" contradicts "research must benchmark the production pipeline."** Frozen src/ can't call a package that doesn't exist yet. Decide now where the new benchmark harness lives. Otherwise you'll end up with two RCA engines, two evaluators, and a repo where an auditor can't tell which one is real.

Also:
- **Multi-tenancy for "one to a few organizations" creates its own risk.** A tenant leak is your worst audit failure. If the owner insists on it, use RLS as a backstop so one forgotten WHERE clause doesn't become a breach.
- **Remediation that's "disabled by default" looks unfinished to an auditor.** Ship a small set of actions that work end to end, or leave it out.
- **Build a thin vertical slice first.** On day one, turn every confirmed defect into a failing test.

**The Executor:**

**Build the end-to-end slice first, and feed it from replay.** On a 16 GB laptop, live telemetry is what will slow you down. The replay snapshots already exist and they are deterministic. Here is Monday morning:

1. Run `docker compose up postgres` and nothing else. Create `backend/aegis`, add the import-boundary test, and add a CI job that runs pytest and mypy. CI takes an hour today. Adding it later costs a week of red builds.
2. Write the eight confirmed defects as failing tests in the new package. That gives you the spec and your defence against the audit in one file.
3. Write one Alembic migration for organizations, incidents, jobs and evidence. Key evidence by (org_id, incident_id, evidence_id). That makes the INSERT OR REPLACE evidence-moving bug impossible.
4. Build the path: webhook → incident → job row → worker claims it with SKIP LOCKED → deterministic pipeline over a snapshot → deterministic summary → GET endpoint → one plain React page. Add the LLM only after this path works.

Positions:
- **(a)** Use Postgres with SKIP LOCKED. You need Postgres anyway, and every extra container uses RAM you don't have. Temporal alone is a week of setup.
- **(b)** Use the BFF. Run Dex as the dev identity provider, not Keycloak, which needs about 1 GB.
- **(c)** Use app-level scoping, with a cross-tenant leak test for every repository. Those tests are what an auditor will run. Add RLS only if time is left.
- **(d)** Build a new package. Research may call the product (the benchmark needs this). The product must never import research. Enforce that with a test on day one.
- **(e)** Use SSE fed by polling a job_events table every second. At tens of jobs, LISTEN/NOTIFY is an optimisation nobody will measure.
- **(f)** Build the vertical slice. Going layer by layer ends with a half-built UI.
- **(g)** Cut from v1:
  - topology view
  - rollback
  - circuit breaking across three LLM providers (ship Ollama plus one OpenAI-compatible provider)

  Do Helm last, check it with `helm template` and kubeconform, and claim nothing beyond that. For remediation, build one read-only action fully, with approval gating. A catalogue of stub actions is what "obviously unfinished" looks like to an auditor.


## Peer reviews

PEER REVIEWS (reviewers saw responses labelled A–E: A=Outsider, B=Contrarian, C=Executor, D=Expansionist, E=First Principles)

**Reviewer 1:**
1. Strongest: E. It is the only response that sees most of the defect list as one root cause: evidence provenance and inputs are never made explicit. It then turns that into architecture. A pure RCA function (snapshot + window → evidence graph + verdict) means replay and production run the same code, so "research benchmarks production" is built into the design. Its rule that the LLM may only lower a deterministic confidence ceiling structurally blocks both injection-steered and unevidenced diagnoses. B is a close second. It adds worker-side `SET LOCAL` under RLS, SSRF IP pinning, a deploy-event source and the self-graded-benchmark critique.
2. Biggest blind spot: D. It adds scope (shadow mode, fine-tuning, Aegis as an MCP server) but says nothing about tenant isolation, SSRF, prompt injection or the single-CPU LLM bottleneck. Keeping every incident's raw telemetry as eval data creates a cross-tenant retention risk. An MCP server is also new attack surface an auditor will probe.
3. All missed data egress and secrets. Logs carry credentials and PII. Once OpenAI or Anthropic providers exist, raw evidence leaves the tenant. That needs redaction before prompting, per-org provider allowlists, and encrypted storage of tenant provider credentials. None covered webhook authentication either: the Alertmanager webhook decides `org_id`, so an unauthenticated endpoint lets anyone inject incidents into another tenant.

**Reviewer 2:**
1. Strongest: B. It covers all three ways the audit can fail, and it is the most concrete about each: unfinished subsystem (deploy correlation has no data source, so that stage would ship empty); security hole (the SKIP LOCKED worker claims jobs across all tenants, so it runs unscoped — fix is RLS with `SET LOCAL` of the claimed job's org; also SSRF in tenant-configured provider URLs, DNS pinning plus blocked private ranges); confidence without evidence (the LLM may lower confidence but never raise it, and CONFIRMED requires a passed deterministic check). It also flags that the benchmark grades itself. E has the best single structural idea: a pure-function RCA core. Merge B and E.
2. Biggest blind spot: D. Adds scope (shadow mode, MCP server, re-run button, fine-tuning loop) for one agent on a 16 GB laptop, where over-building is the stated risk. Says almost nothing about security. Exposing Aegis as an MCP server adds attack surface next to the known fail-open allowlist. Turning real customer incidents into a shared eval set mixes tenant data with no isolation or consent story.
3. What all five missed: data egress and secrets (encrypt tenant provider credentials at rest, redact secrets and PII, let each org choose which LLMs may see its telemetry); per-tenant fairness (one org's alert storm can starve the others on a single Ollama); authentication on the Alertmanager webhook; a tamper-evident audit log with database grants that deny UPDATE and DELETE.

**Reviewer 3:**
1. Strongest: E. Traces most of the defects to one root cause: the engine never states where its evidence came from or which time window it was given. A pure core means "research benchmarks production" comes for free. The deterministic code sets a confidence ceiling that the LLM can only lower. B is a close second and covers what E misses: the worker runs across tenants, so it needs SET LOCAL under RLS; SSRF needs pinned DNS resolution; the deploy-correlation stage has no data and will ship empty; and the benchmark needs a held-out set.
2. Biggest blind spot: D. Adds scope for a single agent. Ignores prompt injection, tenant leaks, SSRF, the reproduced defects and LLM throughput. Keeping every investigation as a replayable snapshot means storing hostile, possibly sensitive tenant telemetry with no end date. Exposing Aegis over MCP adds attack surface.
3. All five missed data egress. With OpenAI or Anthropic as providers, tenant logs that may contain secrets or personal data leave the boundary. That needs redaction before prompting, a per-org list of allowed providers, encrypted provider credentials, and limits on how long evidence is kept. None of them said how the audit log stays append-only either, such as database permissions that deny UPDATE and DELETE, plus hash chaining.

**Reviewer 4:**
1. Strongest: E. Only response that finds the common cause behind the defect list. Pure-function RCA core closes the global-ID, made-up-citation and now()-15min bugs, lets live and replay share one code path, and settles the freeze-versus-benchmark contradiction that A raised but left open. LLM-may-only-lower-confidence is a structural guard against injected log text. B comes a close second and covers security more fully: worker `SET LOCAL` under RLS, DNS-pinned SSRF defence, and the missing deploy-event data source.
2. Biggest blind spot: D. Never mentions tenant isolation, SSRF or prompt injection. Storing every real customer incident as a replay snapshot creates long-lived cross-tenant data with no redaction or retention plan. Adds scope (shadow mode, fine-tuning, a public MCP server) a one-agent, 16 GB build cannot afford.
3. All five missed: redaction (secrets and PII in hostile telemetry must be stripped before evidence is stored, replayed or sent to cloud LLMs; per-organization egress and retention policies); model reliability (nobody asked whether qwen3.5:4b on CPU can reliably produce schema-valid JSON — needs constrained decoding plus a schema-failure path tested as its own benchmark scenario).

**Reviewer 5:**
1. Strongest: E. Traces most of the defect list to one root cause: the engine never states where evidence came from or what inputs it was given. Derives two structural rules: a pure core (snapshot + window → verdict) so research benchmarks the production pipeline by construction; an LLM that can only pick from precomputed candidates and only lower a deterministic confidence ceiling. B is a close second with sharper security detail: worker SET LOCAL under RLS, pinned-IP SSRF blocking, deploy-correlation has no data source, benchmark grades itself.
2. Biggest blind spot: D. Adds scope for one agent on a 16 GB laptop where the main risk is half-finished subsystems. No security position. Keeping real incidents as replay cases ignores tenant data retention and stores hostile log text permanently. The MCP server adds attack surface.
3. All five missed data egress. With OpenAI or Anthropic as fallback, tenant telemetry (secrets, tokens, PII in logs) leaves the boundary. Needs per-org provider allowlists, redaction before prompting, and no silent fallback from local to cloud. Also unaddressed: encrypting stored provider credentials, and authenticating Alertmanager webhooks — forged alerts are both a cost/DoS vector and a way to inject text into prompts.


## Chairman verdict

## Where the Council Agrees

- **Build a thin vertical slice first.** All five advisors said this. Going layer by layer ends with a half-built UI and a core that was never exercised.
- **Treat trust as the problem, not infrastructure.** Contrarian and First Principles reached the same rule separately: deterministic code sets a confidence ceiling, and the LLM may lower it but never raise it. CONFIRMED requires a deterministic check that passed. A validator that only rejects unknown evidence IDs cannot stop injected log text from steering the model to cite real but irrelevant evidence.
- **The reproduced defects become failing tests on day one.** Contrarian, Outsider and Executor all said this. Porting adapters as-is copies their bugs.
- **Use the Postgres SKIP LOCKED queue.** First Principles, Expansionist and Executor backed it. Outsider added that grouping alerts matters more than which queue you pick.
- **One CPU Ollama means you need a global LLM concurrency cap and backpressure on queue depth.** Contrarian and Outsider both flagged this. "Tens of concurrent investigations" really means one LLM call at a time.
- **The benchmark is a gate from day one.** First Principles and Expansionist both said so. Contrarian added a held-out set frozen before any pipeline code exists.
- **Uncontested, but only Executor addressed them:** (b) the backend-for-frontend (BFF) design with cookie sessions, with Dex rather than Keycloak as the dev identity provider; (e) SSE fed by polling a `job_events` table.
- **Signal from peer review:** 4 of 5 reviewers ranked First Principles strongest, with Contrarian a close second. All 5 named Expansionist the biggest blind spot.

## Where the Council Clashes

- **(c) Row-level security (RLS).** Executor wants app-level scoping plus a leak test for every repository, with RLS only if time is left, to save effort. Contrarian, First Principles and Outsider want RLS as a backstop that fails closed when one agent forgets a WHERE clause, and note that the SKIP LOCKED worker is inherently cross-tenant. The real disagreement is cost now versus cost of a breach. RLS is cheap in migration 1 and expensive to retrofit.
- **Remediation, three different shapes:** Contrarian: proposal and approval, with no executor. First Principles and Outsider: one or a few real actions working end to end. Executor: one read-only action. All three agree that a catalogue of stubs looks unfinished to an auditor.
- **What the first slice should contain.** Executor: webhook → job → worker → page, fed from replay. First Principles: snapshot → core → verdict → benchmark. Executor optimises for plumbing risk, First Principles for trust risk.
- **Scope.** Expansionist wants replay as a product feature, plus shadow mode, fine-tuning and an MCP server. Everyone else sees over-building as the stated risk.
- **Deploy correlation.** Contrarian says to add a data source or cut the stage. Nobody else noticed the stage had no data.

## Blind Spots the Council Caught

- **Data leaving the tenant.** Once OpenAI or Anthropic providers exist, raw logs (with secrets and personal data) leave the tenant. That needs redaction before prompting and before storage, a per-org allowlist of providers, and no silent fallback from local to cloud.
- **Webhook authentication.** The Alertmanager webhook decides `org_id`. Unauthenticated, it lets anyone inject incidents across tenants, and it is also a route for pushing text into prompts and a cost/DoS vector.
- **Credential encryption.** Tenant provider credentials must be encrypted at rest.
- **Append-only audit.** Enforce it with database grants that deny UPDATE and DELETE, plus hash chaining.
- **Per-tenant fairness.** One org's alert storm can starve the others on a single Ollama.
- **Schema-valid JSON from a 4B model.** Use constrained decoding, and make schema failure its own benchmark scenario.
- **Retention.** Expansionist's "store every incident" idea keeps hostile, sensitive telemetry forever unless you set retention limits.

## The Recommendation

Adopt the draft, rebuilt around First Principles' core: **a pure RCA function** that takes a telemetry snapshot and an explicit window and returns an evidence graph and a verdict. It touches no DB, network or clock. Live mode and replay call the same function, and evidence is immutable and keyed by (org_id, incident_id, evidence_id).

- **(a) Use the Postgres SKIP LOCKED queue.** Add a global LLM semaphore, round-robin fairness per tenant, rejection or deferral when the queue is too deep, and strict dedup and grouping before jobs exist.
- **(b) Use the BFF with HttpOnly cookie sessions.** Dex in dev. Hashed, scoped API keys for machines.
- **(c) Use both, with RLS from the first migration.** The app role must not own the tables, or the tables need `FORCE ROW LEVEL SECURITY`; otherwise the policy is silently bypassed. The worker claims jobs through a narrow SECURITY DEFINER function, or a claim-only role, that returns only (job_id, org_id). It then runs `SET LOCAL` with that org inside the transaction; a session-level SET would leak across pooled connections. Keep Executor's cross-tenant leak test for every repository as well.
- **(d) Add a new `backend/aegis` package and leave research where it is.** Research may import the product; the product never imports research, enforced by a test.
- **(e) Use SSE fed by polling `job_events` every second.** Skip LISTEN/NOTIFY.
- **(f) Build a vertical slice, core-first.** 1. Snapshot → pure core → deterministic summary → structured-ground-truth benchmark in CI, including the abstention and injection scenarios. 2. Then wrap Executor's path around it: webhook → job → worker → API → one page. 3. The LLM comes last. Show the deterministic summary within seconds; the LLM verdict arrives later.
- **(g)** Cut from v1: generic rollback, the topology view, three-provider circuit breaking (ship Ollama plus one OpenAI-compatible provider), shadow mode, fine-tuning, and Aegis as an MCP server. Helm: check it with `helm template` and kubeconform, and claim nothing beyond that. Add everything in the blind-spot list, and SSRF defence (resolve the host once, connect to that pinned IP, block private and metadata ranges). The MCP client's allowlist must fail closed when empty. Deploy correlation: add a minimal change-event ingest endpoint and have the demo's fault-flag flip post to it. Remediation: build one real, reversible action fully (clearing the demo fault flag through proposal → role-gated approval → execution → post-action verification → audit), enabled in the demo org and disabled per org by default as policy. Expansionist: keep only the cheap part — persist each investigation's snapshot for replay, under redaction and retention limits. Benchmark: freeze a held-out scenario set before writing pipeline code.

## The One Thing to Do First

Write one test file in `backend/aegis` that encodes the eight reproduced defects as failing tests against the pure RCA core's input/output contract. That one file defines the core's interface, gives the slice its spec, and gives you your first answer to the auditor.


## How the verdict was applied (engineering decision, checked against repository evidence and the owner's requirements)

| Verdict item | Decision | Reason |
|---|---|---|
| Pure RCA core, live and replay share it | **Adopted** (ADR-008) | Fixes the root cause behind AI-03/DATA-01; research can benchmark the product. |
| LLM may only lower a deterministic confidence ceiling; CONFIRMED needs a passed check | **Adopted** (ADR-009) | Structural defence against injection-steered and unevidenced diagnoses. |
| Postgres SKIP LOCKED + LLM semaphore + per-tenant fairness + backpressure | **Adopted** (ADR-010) | One stateful dependency; LLM is the bottleneck. |
| BFF cookie sessions, Dex in dev, hashed API keys | **Adopted** (ADR-011) | |
| RLS from migration 1 (FORCE RLS, non-owner role, claim function, SET LOCAL) + leak tests | **Adopted** (ADR-012) | Fail-closed tenancy; retrofit cost is high. |
| New `backend/aegis` package; research may import product, not vice versa | **Adopted** (ADR-007) | |
| SSE fed by polling `job_events` | **Adopted** (ADR-010) | |
| Core-first vertical slice, LLM last | **Adopted** (roadmap M1–M3) | |
| Cut topology view | **Rejected — deferred instead** | Owner requirements §13/§15 make topology mandatory. Topology is also an RCA input (dependency direction). Scheduled after the slice. |
| Cut Aegis-as-MCP-server | **Rejected — deferred** | Owner requirement §23. The research MCP server stays as-is; a tenant-scoped, API-key-authenticated production MCP surface comes later. MCP *client* fail-closed fix is in scope early. |
| Cut generic rollback | **Modified** | Every reversible action in the catalogue declares its inverse; rollback = executing the inverse through the same approval path. No generic "undo". |
| Ollama + one OpenAI-compatible provider first | **Adopted with extension** | Anthropic adapter follows with contract tests (owner §9); no silent local→cloud fallback (per-org allowlist). |
| Shadow mode, fine-tuning | **Rejected** | Not required; scope risk. Replay of stored snapshots under retention covers the research need. |
| Change-event ingest endpoint fed by the demo's flag flips | **Adopted** (ADR-013) | Gives change correlation a real data source. |
| One real reversible remediation action end to end | **Adopted** (ADR-014) | |
| Held-out set frozen before pipeline code | **Done** before this verdict was applied (`eval/heldout/`, commit `cf07159`) | |
| Blind spots (egress/redaction, webhook auth, credential encryption, append-only audit, fairness, constrained decoding, retention) | **Adopted** | Folded into security model, ADR-009/012/015 and the roadmap. |
