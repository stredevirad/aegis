# Aegis — Production Readiness (verification ledger)

This is the engineering control document behind [`production-roadmap.md`](production-roadmap.md). A row is `VERIFIED` only when the command in the row was executed and its output is recorded here, with the date. `PARTIAL` states what works and what does not; `BLOCKED` names the external dependency; `PLANNED` means not implemented. Nothing here is inferred from documentation.

Environment for all runs unless stated: Windows 11, Python 3.13 (backend via `uv`, Python ≥ 3.12), PostgreSQL 16.15 from `deploy/compose.dev.yaml`, Docker Desktop 28.3. From 2026-11-09: Docker Engine 29.9.2, Node 26.13 / npm 11.20, uv 0.12.15.

## Current verification snapshot

| Suite | Command (from) | Result | Date |
|---|---|---|---|
| Full backend gate (format, lint, types, tests, benchmark) | `backend/scripts/check.sh -m "not live"` | 356 passed, 4 deselected, gate green | 2026-11-09 |
| Backend PostgreSQL, API and process tests (run as `aegis_app`) | `docker compose -f deploy/compose.dev.yaml up -d --wait` then `uv run --frozen pytest -m "db and not live"` (`backend/`) | 128 passed, 228 deselected (part of the 352 above; the live Dex test was not run) | 2026-11-09 |
| Synthetic benchmark gate | `uv run --frozen python -m aegis.benchmark.run --scenarios ../eval/scenarios --gate` (`backend/`) | 15/15 (12 correct, 3 appropriate abstentions), 0 confident-wrong, exit 0 | 2026-10-02 |
| Research suite (frozen layer) | `uv run --no-project --with pydantic --with httpx --with pyyaml --with "mcp>=2.0.0" --with pytest --with-editable . python -m pytest tests -m "not integration"` (repo root) | 159 passed, 6 integration tests deselected | 2026-11-09 |
| Format / lint / types | `uv run --frozen ruff format --check .`, `ruff check .`, `mypy` (`backend/`) | clean (mypy strict: 109 files) | 2026-11-09 |
| Frontend | `npm ci && npm run typecheck && npm run lint && npm test && npm run build` (`frontend/`) | tsc (app + e2e configs) + ESLint clean; 78 passed in 8 files; build OK (444.45 kB JS) | 2026-11-09 |
| Browser E2E against the real backend | dev stack (`deploy/compose.dev.yaml --profile app`, `backend/scripts/dev-stack.sh`, `npm run dev`), then `npx playwright test` (`frontend/`) | 3 passed (s06 golden path, s08 abstention, s10 prompt injection), 3 consecutive green runs; zero console errors / unexpected ≥400 | 2026-11-09 |
| Secret scan, full history | `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo" zricethezav/gitleaks:latest git /repo --redact --gitleaks-ignore-path /repo/.gitleaksignore` (repo root) | gitleaks v8.30.1, 59 commits: no leaks (3 triaged test fixtures allowlisted) | 2026-11-09 |

## Verification log

Newest first. Each entry: what was run, the observed result, and what it does not show.

### 2026-11-09 — Portfolio Phase 2: operator UI against the real backend

* `frontend/e2e/golden-path.spec.ts` (Playwright 1.65.0, Chromium headless) drives the dev stack through the UI. Each test signs in with the dev login, creates an incident on the Incidents page and starts a scenario investigation from the Investigate panel. Any console error or unexpected ≥400 API response fails the test; only the pre-sign-in 401 is allowed. Assertions: **s06** "Strongly supported", root cause `catalog`, "Change-induced (feature flag)"; the first citation highlights, focuses and scrolls to the cited evidence item; Evidence explorer opens. **s08** "Not enough evidence", no root-cause card, "Next checks" shown. **s10** "Likely cause", root cause exactly `catalog` (the injected log names `payments`); the injected log template renders as plain text, with no `a`/`script`/`img`/`iframe` markup.
* The first run failed all 3 tests on `404 GET /investigations/{id}/snapshot` plus the browser's console error for it. Cause: `InvestigationPage` requested the snapshot before the worker stored it. Fix (9b11dc4): the query waits for `completed` or the replayed `snapshot_stored` event. Vitest: 3 tests; the "before stored" test fails when the gate is removed (checked by temporarily reverting it).
* The "Remediation proposals are not yet available" placeholder was removed from the incident and investigation pages (component and CSS deleted).
* `aegis admin seed-demo --org demo` (28f134a): 4 synthetic incidents, investigations queued through the normal queue, idempotent. `tests/db/test_demo_seed.py` → 4 passed. The worker reached s06 CONFIRMED/strong; s09 and s10 PLAUSIBLE/moderate on `catalog`; s08 INSUFFICIENT/none with 3 next checks. The same verdicts were read back over the API from the running dev stack.
* Vite bound only `[::1]:5173` on this Windows machine, so `http://127.0.0.1:5173` was refused; `vite.config.ts` now sets `host: "127.0.0.1"`.
* Gates: backend `check.sh -m "not live"` → **356 passed**, 4 deselected; frontend typecheck/lint/test/build → 78 passed in 8 files; `npx playwright test` → 3 passed, three consecutive runs (13.3 s, 14.8 s, 15.4 s).

Does not show: the stack in containers (Phase 3), CI (Phase 4), or any live-telemetry investigation in the UI. All UI runs used synthetic scenarios.

### 2026-11-09 — Portfolio Phase 1: consolidate, secure, first push

Plan: `docs/PORTFOLIO_PROGRESS.md` (portfolio re-scope, [Council P1](councils/council-P1-portfolio-scope.md)).

* Backup before any change: `git bundle create ~/aegis-backups/aegis-2026-11-09.bundle --all` → `git bundle verify` OK; the uncommitted backend diff, the 3 untracked `0008_remediation_sweep*` files and the 2 uncommitted worktree files were copied to `~/aegis-backups/` (outside the repo).
* Baseline before changes: `backend/scripts/check.sh -m "not live"` → ruff format/lint clean, mypy strict clean (109 files), **352 passed, 4 deselected**, benchmark gate green (2 min 43 s). Research suite → **159 passed, 6 deselected**. DB subset `pytest -m "db and not live"` → **128 passed** (included in the 352).
* Committed the owner's Council D follow-ups (8b5fbad): per-integration LLM circuit breaker and `reconcile_stuck()` + migration 0008. See [council-D-note.md](councils/council-D-note.md). Correction to that commit message: a stranded rollback is marked `rollback_failed` (not `failed`); both carry `uncertain: true`. An automated security review flagged two low-likelihood robustness gaps in this code (unconditional final status write; maintenance loop stops on an unexpected exception). They're recorded under "Open follow-ups" in `docs/PORTFOLIO_PROGRESS.md` and not fixed here.
* Frontend recovered: `git merge --no-ff worktree-agent-a066710032934483b` (4d3afe5, conflict-free) plus the 2 uncommitted worktree files (20ec99a, byte-identical to the backup). `npm ci && npm run typecheck && npm run lint && npm test && npm run build` → tsc and ESLint clean, **75 passed in 7 files**, build OK (444.81 kB JS). Worktree removed; branch deleted with `git branch -d` (merged).
* Branch `production/platform` renamed `main`. Local `master` (= `v1.0-research-complete`) and `feature/mcp-interoperability` (= `v1.1-research-freeze`, dc7c032) are ancestors and stay unpushed.
* Licence: MIT `LICENSE` (© 2026 Joshua Herbert Japheth), `backend/pyproject.toml` `license = { text = "MIT" }`; `uv lock --check` OK. `.mailmap` maps the 23 placeholder `stredi` commits and the `stredevirad` commits to the owner. `git shortlog -sne --all` shows one identity. GitHub ignores `.mailmap`, and history was not rewritten.
* Secret scan: gitleaks v8.30.1 over full history (59 commits, all refs) → 3 findings, all test fixtures (a fake bearer token in `tests/api/test_integrations.py`; the jwt.io sample JWT and a 20-character dummy PEM block in `tests/test_redact.py`). Allowlisted by fingerprint in `.gitleaksignore` with a comment each; re-run → **no leaks found**.
* Remote: `stredevirad/aegis` existed, PRIVATE and empty. `git push -u origin main` plus both research tags pushed. `gh repo view --json defaultBranchRef,visibility` → `main`, `PRIVATE`.

Does not show: anything about deployability (no backend image or full compose stack yet), CI, or the UI against the real backend. Those are portfolio Phases 2–4.

### 2026-10-02 — M4 egress/collection, M3 AI adjudication, M6 remediation core

`backend/scripts/check.sh -m "not live"` → format, lint, mypy (strict), **350 passed**, benchmark gate 15/15 — green. (The run took 9.5 min with under 2 GB of free RAM while a frontend build ran in parallel; the same suite took 85 s earlier in the day. Test runtime is not a performance claim.)

Live runs (marker `live`, executed and observed):

* **Live RCA against the demo stack** (`tests/integrations/test_live_demo.py`, 303 s incl. designed waits): 3 min healthy baseline, catalog error flag set to 40 % and recorded as a change event, Aegis collected Prometheus, Jaeger and Loki through the connector registry and egress client → **CONFIRMED, support strong, root cause `catalog`, mechanism `change_induced:feature_flag`**, checks passed: propagation, isolation, change_scope, modality_agreement. One run on one fault; not an accuracy estimate.
* **Live Ollama** (`tests/llm/test_ollama_live.py`, llama3.2:1b, CPU, Ollama 0.35.0):
  * first run: `prompt_tokens=2050` on both scenarios → Ollama's default 2048-token context had silently truncated the ~5k-token prompt. **Fixed**: `num_ctx` always explicit (connector `context_window`, default 8192) and the prompt sized to fit; regression assertion `prompt_tokens > 2100` and a unit test on `num_ctx`.
  * in that run the model escalated the prompt-injection scenario to CONFIRMED (deterministic PLAUSIBLE), invented candidates (including the data-block marker) and cited a provider name as evidence — **all rejected by the validator**; the deterministic verdict stood.
  * second run (8192 context): s10 prompt_tokens 5470, 266 s, model kept PLAUSIBLE/moderate but selected no candidate → rejected; s05 output cut off by the 900-token cap → invalid JSON (the adjudicator handles this as a schema failure → one repair → deterministic fallback). The test now records such outcomes instead of failing.
  * Conclusion recorded honestly: with a 1B local model, AI assessments were rejected in every observed case; the product's answer was the deterministic one. No model-accuracy claim is made.

Exercised by tests (not live):

* **Egress/SSRF** (66 cases): metadata/link-local/mixed DNS answers, IPv4-in-IPv6 forms, loopback/private policy, Aegis's database by name and by resolved address, path allowlists (no Prometheus admin API, no Ollama pull/delete), Host/SNI pinning, redirects refused, size caps, timeouts, bounded retries, forbidden methods/headers; registry rejects credentials in URLs, wildcards, infrastructure collisions, plain http and loopback outside development.
* **Integrations API** (6): tenant A cannot bind tenant B's connector or pass a URL; credentials write-only and encrypted; a ciphertext copied to another tenant's row is not decrypted (nothing sent); metadata destination refused at request time.
* **Collectors** (10 contract tests on recorded responses): hostile service names stay inside escaped PromQL/LogQL matchers; OpenSearch uses structured filters only; span status kept; partial/failed/empty distinguished; truncation flagged; redaction at collection.
* **Live worker path** (3, recorded responses): live investigation completes with collection outcomes and redaction record; all backends down → retry → last attempt reports coverage gaps; org without integrations fails with a clear reason.
* **AI layer** (20 validator attack cases, 13 prompt/provider tests, 8 worker tests, 2 API tests): see `docs/ai.md`.
* **Remediation** (9, against the real demo flag service over HTTP through the egress client): full two-person lifecycle with rollback; self-approval refused; stale proposal expires (state committed before the error — **a defect found by these tests**: errors raised inside the transaction had rolled back the `expired` status and its audit record); target changed after approval → aborted without writing; expired and tampered approvals cannot execute; parameters cannot come from the request; cross-tenant 404; suggestions follow the deterministic root cause; verification of a synthetic diagnosis is `not_evaluable`.

Not shown: OpenAI/Anthropic live calls (no credentials — BLOCKED), Tempo and Kubernetes collectors (not implemented), live post-action verification against the demo stack (next), notifications (next).

### 2026-10-02 — M2 complete: OIDC authorization code + PKCE

`uv run --frozen pytest` (`backend/`, PostgreSQL + Dex v2.44.0 from `deploy/compose.dev.yaml --profile auth`) → **213 passed**.

* `tests/api/test_oidc.py` (17, fake IdP over an in-process transport): full code flow with PKCE (the IdP verifies the S256 challenge), session issued, single-use login state; rejection of wrong audience, wrong issuer, expired token, wrong/missing nonce, missing `sub`, bad signature, unknown `kid`, `alg: none`, HMAC-with-public-key confusion, multi-audience without matching `azp`, state mismatch, missing or tampered login-state cookie, PKCE verifier mismatch, unverified email (no invitation accepted), IdP error; `return_to` open-redirect attempts (`//host`, absolute URLs, `/\host`, `javascript:`) all land on `/`; dev login is not routable in OIDC mode.
* `tests/api/test_oidc_dex_live.py` (marker `live`): the test drives Dex's real login form; Aegis exchanges the code, validates the ID token against Dex's JWKS, accepts the reviewer invitation and issues a session with role `reviewer` in the invited organization; a wrong password never yields a code.
* Session idle and absolute expiry each end access (`test_idle_and_absolute_session_expiry`).

Not shown: refresh tokens are not used (sessions are server-side; re-login after absolute expiry); IdP-side account removal is reflected only through Aegis membership removal or `aegis admin set-user-disabled` (documented in ADR-011 amendment).

### 2026-10-02 — M2 slice: queue, worker, API, ingestion, auth (dev login + API keys), CLI

Commands (from `backend/`, PostgreSQL 16.15 from `deploy/compose.dev.yaml`): `uv run --frozen pytest` → **194 passed** (114 without a database, 80 against PostgreSQL as `aegis_app`); `ruff format --check`, `ruff check`, `mypy` (strict) clean; benchmark gate 15/15, 0 confident-wrong.

What is exercised (test files in parentheses):

* **Queue and worker** (`tests/db/test_queue_and_worker.py`, `test_worker_process.py`): transactional enqueue; idempotency keys; one active investigation per incident under 8 concurrent starts; queue limit (manual → 429, automatic → deferred, promoted later); end-to-end scenario investigation with persisted evidence, events and audit; a stalled worker fenced out after another reclaims its job; heartbeat-detected lease loss aborts the pipeline before any write; crash with attempts left → reclaimed; attempts exhausted → reaped (job `dead`, investigation `failed`, event) atomically; retryable failure → backoff → permanent failure; unexpected errors fail without leaking exception text (row **and** redacted JSON log line); cancellation queued/running; `Worker.run` with concurrency 2 and clean stop; killed worker (grace 0) mid-job → second worker reclaims and completes.
* **API slice** (`tests/api/test_slice.py`): Alertmanager webhook with an ingest key → incident → automatic investigation → worker → CONFIRMED/PLAUSIBLE verdict whose cited evidence IDs all resolve to stored evidence with provenance; evidence paging; graph; snapshot summary; ordered events; SSE replay from `Last-Event-ID`; audit chain intact.
* **Real network** (`tests/api/test_http_stack.py`): uvicorn on a TCP port + `Worker.run`; the SSE client receives `queued` before any worker exists (a buffering transport would time out), then streams through `completed`; feature-flag scenario → CONFIRMED, `change_induced:feature_flag`.
* **Security** (`tests/api/test_security.py`, `test_ingest_concurrency.py`): every `/api/v1` route declares a permission; RBAC matrix per role (viewers and reviewers cannot start investigations); CSRF (missing header, wrong origin); cross-tenant access to 16 routes returns 404 and lists are empty; API keys hashed, shown once, rotated with grace, revoked, expired, forged; ingest keys cannot read or administer; membership removal ends sessions and revokes the member's keys; role changes apply to live sessions; last-admin protection; invitations accepted only for the matching verified email; payloads cannot choose the organization; hostile alert content neutralised (control/bidi characters, `javascript:` URLs, oversized annotations, far-future timestamps); body-size limit; validation errors never echo values; rate limiting; unsafe production configuration refuses to start; 20 concurrent overlapping payloads mixing re-deliveries × 3 rounds without deadlock (**this test reproduced `deadlock detected` on the previous code**).
* **Operator CLI** (`tests/db/test_cli.py`): idempotent organization bootstrap with admin invitation; invitations; disabling a user deletes sessions and revokes keys in every organization; operator functions not executable by `aegis_app`; process commands refuse unsafe configuration.
* **Redaction** (`tests/test_redact.py`, 47 cases) and **alert normalisation** (`tests/test_ingest_alerts.py`).

Defects found and fixed in this step (each has a regression test):

1. Core evidence lacked provenance for error-origin, log-rate, edge-latency, edge-change and coverage-gap items, so cited evidence could not be traced to a telemetry source. All evidence now carries sources; verdicts unchanged (`tests/core/test_benchmark_and_logs.py::test_every_evidence_item_carries_provenance`).
2. Alert dedup used the clamped/filled start time as identity: a sender whose clock ran ahead, or that sent no start time, created a new alert row on every re-send.
3. Ingestion deadlock under concurrent overlapping payloads (audit-chain lock taken mid-transaction; re-delivery row locks before group locks). Fixed with a per-organization ingestion lock and the "audit insert last" rule; pure duplicates no longer write audit records.
4. SSE stream counter could leak a slot when the client disconnected before streaming began.
5. Worker error logs would have carried secrets from exception messages once JSON logging existed; the formatter now redacts message, extras and traceback.

What this does **not** show: OIDC login (next step; dev login and API keys only so far); live telemetry collection (M4 — investigations run on synthetic scenario sources, clearly marked `synthetic`); behaviour across multiple API replicas (rate limits are per replica by design); performance at the target scale (M10).

### 2026-10-02 — session 2 start: re-verification of M0/M1 and the uncommitted M2 database foundation

* Non-DB backend tests: **32 passed** (before this session's additions).
* DB tests against the ad-hoc dev container: **14 passed** — reproduces the previous session's figure.
* Benchmark gate: 15/15, 0 confident-wrong.
* Static checks on the uncommitted M2 code **failed** at session start: 5 files unformatted, ruff S608 in `aegis/db/bootstrap.py`, unused-variable in a test, mypy missing asyncpg stubs and a stale `type: ignore`.
* Defects found by review (not by the failing checks) and fixed:
  * **Bootstrap SQL injection (S608 was real).** Role passwords were embedded in a dollar-quoted `DO $$ … $$` body; a password containing `$$` closes the body and executes arbitrary SQL as the superuser. Fixed: no `DO` bodies; statement text comes from the server's `format('%L')`; `--print-sql` quotes client-side and rejects NUL/backslash; re-running re-asserts role attributes (a pre-existing `aegis_app` with BYPASSRLS is corrected). Regression: the DB test fixture now bootstraps `aegis_app` with the password ``app-pw-$$'; DROP ROLE aegis_owner; --$q$``, so every DB test connects through it; `tests/test_bootstrap_sql.py` covers quoting and identifier validation.
  * **Configuration failed open.** `profile` defaulted to `development`, for which validation returns no problems, so a deployment that forgot `AEGIS_PROFILE` ran with dev login. Fixed: default `production`. Regression: `tests/test_config.py::test_profile_defaults_to_production` plus one test per rejected unsafe setting.
  * **Weak tests.** `test_claim_works_under_forced_rls_without_tenant` passed whenever any job was claimed; the per-org cap test isolated itself by mutating other tests' rows; the audit tamper test relied on a manual "undo". Fixed with an `empty_queue` fixture, exact assertions, and an explicit rolled-back transaction.
* Added pooled-connection isolation tests (`tests/db/test_pool_isolation.py`): one-connection pool, same backend PID asserted; the tenant setting does not survive commit, a Python exception, or a database error; the next tenant on the same connection sees only its rows; the app role cannot create TEMP/public/aegis objects (search-path hijack); a source scan forbids session-scoped `aegis.*` settings.
* `deploy/compose.dev.yaml` added; the DB suite was re-run on a fresh compose-managed instance: **21 passed**.

## Milestone status (summary; details per criterion in the roadmap)

| Milestone | Status | Evidence |
|---|---|---|
| M0 audit, decisions, research preservation | DONE | roadmap; research suite 159 passed; MIT licence added 2026-11-09 |
| M1 pure RCA core + benchmark gate | DONE (LLM-free) | core tests; benchmark gate |
| M2 persistence, jobs, API, auth, tenancy, audit, ingestion | DONE | schema 0001–0004, queue, worker, API, ingestion, OIDC (fake IdP + live Dex), API keys, RBAC, CLI — see log |
| M3 LLM adjudication | PARTIAL | adapters, validator, policy, slots, records done; live OpenAI/Anthropic blocked (no credentials); LLM benchmark M10 |
| M4 live integrations | PARTIAL | registry, egress, Prometheus/Jaeger/Loki/OpenSearch, integrations API, live demo RCA; Tempo/Kubernetes planned |
| M5 web application | PARTIAL | merged into `main` 2026-11-09; 78 unit tests + 3 Playwright E2E against the real backend green (Phase 2); remediation UI deferred (stretch S3) |
| M6 remediation and notifications | PARTIAL | remediation lifecycle done and tested; live verification and notifications pending |
| M7–M11 | PLANNED | — |
