# Aegis — Production Readiness (verification ledger)

This is the engineering control document behind [`production-roadmap.md`](production-roadmap.md). A row is `VERIFIED` only when the command in the row was executed and its output is recorded here, with the date. `PARTIAL` states what works and what does not; `BLOCKED` names the external dependency; `PLANNED` means not implemented. Nothing here is inferred from documentation.

Environment for all runs unless stated: Windows 11, Python 3.13 (backend via `uv`, Python ≥ 3.12), PostgreSQL 16.15 from `deploy/compose.dev.yaml`, Docker Desktop 28.3. From 2026-11-09: Docker Engine 29.9.2, Node 26.13 / npm 11.20, uv 0.12.15.

## Current verification snapshot

| Suite | Command (from) | Result | Date |
|---|---|---|---|
| Full backend gate (format, lint, types, tests, benchmark) | `backend/scripts/check.sh -m "not live"` | 366 passed, 4 deselected, gate green (Phase 6) | 2026-11-09 |
| CI on GitHub Actions (all jobs) | `.github/workflows/ci.yml`, [run 42594753903](https://github.com/stredevirad/aegis/actions/runs/42594753903) on d211838 | backend 360 passed / 0 skipped (`AEGIS_REQUIRE_DB=1`; 134 PostgreSQL tests) + mypy strict + benchmark gate; research 159; frontend 78 + build; images; gitleaks 73 commits clean; E2E 3 passed against the compose stack | 2026-11-09 |
| Backend PostgreSQL, API and process tests (run as `aegis_app`) | `docker compose -f deploy/compose.dev.yaml up -d --wait` then `uv run --frozen pytest -m db -q` (`backend/`) | 137 passed, 2 skipped (the two `db` + `live` tests: no live services), 231 deselected (Phase 6) | 2026-11-09 |
| Synthetic benchmark gate | `uv run --frozen python -m aegis.benchmark.run --scenarios ../eval/scenarios --gate` (`backend/`) | 15/15 (12 correct, 3 appropriate abstentions), 0 confident-wrong, exit 0 (Phase 6) | 2026-11-09 |
| Research suite (frozen layer) | `uv run --no-project --with pydantic --with httpx --with pyyaml --with "mcp>=2.0.0" --with pytest --with-editable . python -m pytest tests -m "not integration"` (repo root) | 159 passed, 6 integration tests deselected | 2026-11-09 |
| Format / lint / types | `uv run --frozen ruff format --check .`, `ruff check .`, `mypy` (`backend/`) | clean (mypy strict: 110 files) | 2026-11-09 |
| Frontend | `npm run typecheck && npm run lint && npm test && npm run build` (`frontend/`) | tsc (app + e2e configs) + ESLint clean; 81 passed in 8 files; build OK (444.88 kB JS) (Phase 6) | 2026-11-09 |
| One-command stack from a clean Docker state | `scripts/demo-down.sh` then `scripts/demo-up.sh` (or the `.ps1` pair) | Phase 6: exit 0 in 37 s (warm build cache, both images rebuilt); `E2E_BASE_URL=http://127.0.0.1:8080 npx playwright test`: 3 passed, 2 skipped (the env-gated media and GIF specs). Phase 3: 72 s / 27 s (`.ps1`); `/auth/config` through nginx OK; core memory ≈ 0.36 GB, demo profile ≈ 0.93 GB after 10 min | 2026-11-09 |
| Browser E2E against the real backend | dev stack (`deploy/compose.dev.yaml --profile app`, `backend/scripts/dev-stack.sh`, `npm run dev`), then `npx playwright test` (`frontend/`) | 3 passed (s06 golden path, s08 abstention, s10 prompt injection), 3 consecutive green runs; zero console errors / unexpected ≥400 | 2026-11-09 |
| Secret scan, full history | `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo" zricethezav/gitleaks:latest git /repo --redact --gitleaks-ignore-path /repo/.gitleaksignore` (repo root) | gitleaks v8.30.1, 59 commits: no leaks (3 triaged test fixtures allowlisted) | 2026-11-09 |

## Verification log

Newest first. Each entry: what was run, the observed result, and what it does not show.

### 2026-11-09 — Portfolio Phase 6: final fixes and polish (v1.0.1)

Both remediation findings from the Phase 1 automated security review are fixed, plus small polish. Each fix's test was run against the old code first and failed for the stated reason.

* **Remediation lost update** (ff31ba8). `execute()`/`rollback()` claimed the row with a compare-and-set but wrote the final status by org and id only, so a late result could overwrite a row the stuck-row sweeper had already marked `failed`/`rollback_failed`. The final write is now compare-and-set on the claimed status (`_finish`). On 0 rows the reconciled record stays, the late outcome (incl. the target state) is audited as phase `finished_late`, and the current row is returned. Tests: `test_late_execution_result_does_not_overwrite_a_reconciled_row` and `test_late_rollback_result_does_not_overwrite_a_reconciled_row` run the real sweeper while the flag write is in flight. Before the fix: `'verifying' == 'failed'` and `'rolled_back' == 'rollback_failed'` failed.
* **Maintenance-loop fault isolation** (cb9ee49). `reconcile_stuck()` reconciles each row in `_reconcile_one` behind a per-row `except Exception` with `log.exception` (a vanished row is logged and skipped; the failed row keeps its lease and is retried after 300 s). `Worker._maintenance()` catches `Exception` with `log.exception`. Neither catches `BaseException`, so cancellation still stops the loop. Tests: `test_one_bad_stuck_row_does_not_stop_the_sweep` (a `RuntimeError` from the flag client) and `test_maintenance_loop_survives_an_unexpected_exception` both failed before the fix with the `RuntimeError` escaping. `test_maintenance_loop_still_stops_on_cancellation` passes before and after.
* Dependabot version updates stopped (545be3c): `open-pull-requests-limit: 0` for actions, npm, uv and docker. Security updates are a repository setting (Phase 6 item 6.12).
* Check reasons (5a73494): "supported by logs, metrics, traces" and "only metrics modality supports it" instead of Python list reprs. Benchmark and held-out output are byte-identical before and after (diffed), and `git diff --stat -- eval/` is empty. Other check reasons still contain list reprs (e.g. "dependencies ['catalog'] are also anomalous"); they were out of scope.
* Operator UI (34dd687): the verdict headline and summary for CONFIRMED/PLAUSIBLE are worded from `root_causes` with `mechanismLabel()` ("Strongly supported: catalog — Change-induced (feature flag)"). The backend `display_label` is unchanged. There are 3 new Vitest tests; 2 of them fail on the old component.
* README (2ff067d, f4d53da), CI comment (48a13ce). The media (f4d53da) come from a fresh `demo-down.sh` → `demo-up.sh`:
  * The GIF now comes from `frontend/e2e/demo-gif.spec.ts` (`E2E_GIF=1`): one browser video, cut by ffmpeg 9.0.2 to the loaded s06 verdict, one citation click and the loaded s08 abstention. Result: 9.8 s, 3.53 MB, 1100×725 (ffprobe). The first attempt kept a blank frame and a "Loading your session" frame at the splice, because the video clock lags the test clock; a 1.5 s pad after each end mark fixed it.
  * Frames checked by eye: the first frame is the s06 verdict with the new headline, and the last frame is the s08 abstention. An ffmpeg scene scan shows changes only at the click and the splice.
  * Screenshots were regenerated. `dashboard.png` and `evidence-explorer.png` were unused and are deleted.
* Gates, run one after another on 2026-11-09:
  * `check.sh -m "not live"`: 366 passed, 4 deselected, gate green.
  * `pytest -m db`: 137 passed, 2 skipped (`db` + `live`: no live services), 231 deselected.
  * Benchmark gate: 12 correct, 3 appropriate abstentions, 0 confident-wrong.
  * Research suite: 159 passed, 6 deselected.
  * Frontend: typecheck + lint clean, 81 passed in 8 files, build OK.
  * Playwright against the compose stack: 3 passed, 2 skipped (env-gated media/GIF specs). The stack was stopped afterwards.
* What this does not show: the race is tested by forcing the interleaving inside one process (the sweeper runs inside the patched flag write). There is no multi-process or load test of it. The CI result is recorded in the snapshot table above after the push.

### 2026-11-09 — Portfolio Phase 5: README, media, docs, release

* Media (f05d9ef): `frontend/e2e/media.spec.ts` (`E2E_MEDIA=1`) regenerates `docs/media/*.png` from the seeded stack. `demo.gif` is the golden-path E2E recorded with `E2E_RECORD=1`, converted with ffmpeg 9.0.2 (10 fps, 1100 px, 5.0 MB). Found while reviewing the screenshots and fixed (b5df992): an abstention's root-cause layer was labelled "VERIFIED FINDING"; it's now "NO FINDING", with tests asserting no verified-finding region for INSUFFICIENT and NO_ANOMALY.
* README rewritten (b1e402d). Numbers re-run on 2026-11-09:
  * `python -m aegis.benchmark.run --scenarios ../eval/scenarios` → 12 correct, 3 appropriate abstentions, 0 confident-wrong; 11 asserted; precision 1.0 (Wilson 0.741–1.0).
  * `python -m aegis.benchmark.heldout --root ..` → 8 cases, 0 confident-wrong, 6 abstentions, 2 partial (= post-hoc run 2; clean run 1 also had 0 confident-wrong).
  * The research README moved to `docs/research/README-research.md` with a banner.
* Docs truthfulness pass (9c6d5fa): `docs/README.md` index; a portfolio-scope table in the roadmap; banners on 15 research-era docs; the demo script rewritten for the one-command stack.
* Independent stranger review (Sonnet subagent, fresh `gh repo clone`, README only): the quickstart worked as written (exit 0; cached images, so not a cold start). All 35 relative links resolve. The seeded verdicts, both benchmark outputs, the 62 egress tests and the E2E run (3 passed, media test skipped) matched the README. Wording nits were fixed (see PORTFOLIO_PROGRESS Phase 5 step 5). Re-run afterwards: `demo-up.sh` exit 0 in 36 s; dev login 200; the 4 seeded verdicts as documented; `demo-down.sh` exit 0.
* Release `v1.0.0` (private repository) created from `main` at the commit that adds this entry.

Does not show: a cold-cache first run (every run reused cached base layers), or GitHub rendering of the Mermaid diagram.

### 2026-11-09 — Portfolio Phase 4: CI

* `backend/tests/conftest.py`: an unreachable PostgreSQL used to skip every DB test, so a gate could pass having run none of them. With `AEGIS_REQUIRE_DB=1` it now fails. Checked with the database stopped: `tests/db/test_cli.py` gave 3 skipped by default and 3 errors with the variable set. `tests/test_require_db.py` covers both modes.
* `.github/workflows/ci.yml`: backend (Postgres 16.15 service, `AEGIS_REQUIRE_DB=1`, `scripts/check.sh -m "not live" -rs`, benchmark JSON artifact); research; frontend (Node 24); images (backend with the `scenarios` build context, frontend, compose config for both profiles); gitleaks v8.30.1 over full history; and E2E (compose stack plus Playwright on :8080; push to main or manual dispatch). `.github/dependabot.yml` covers actions, npm, uv and docker.
* First run (42594364326): backend and research failed at setup, because `astral-sh/setup-uv@v10` doesn't exist (the action publishes no moving v10 tag). It's pinned to `v10.3.3`.
* **Green run [42594753903](https://github.com/stredevirad/aegis/actions/runs/42594753903) (d211838), all 6 jobs:**
  * backend: **360 passed, 4 deselected, 0 skipped**. 134 of the 360 are PostgreSQL tests (`pytest -m "db and not live" --collect-only` → 134), so all of them ran in CI. ruff and mypy strict (110 files) clean; benchmark gate green.
  * research 159 passed (6 deselected); frontend 78 passed in 8 files plus the build; gitleaks: 73 commits, no leaks; E2E: 3 passed (14.0 s) against `docker compose up --build --wait` on a GitHub runner.

Does not show: anything about production deployment. The E2E job uses the DEMO MODE stack.

### 2026-11-09 — Portfolio Phase 3: one-command stack

* `backend/Dockerfile` (ffef2ac): python:3.13.16-slim + uv 0.12.15 (tags verified with `docker manifest inspect`), non-root uid 10001, 338 MB, scenarios through a named build context. Inside the image: `aegis --help` works, 15 scenarios present, migration SQL packaged.
* `aegis admin create-api-key` (ffef2ac, 45c3db6). The secret goes only to `--out`, written via `mkstemp` and renamed, owner-only unless `--mode` widens it. Tests: the key ingests alerts (202) and is refused on change events (403); the secret appears in neither stdout/stderr nor audit details. An automated security review flagged the first version's predictable temp name and 0644 default; both were fixed before the stack verification below.
* `deploy/compose.yaml` core services (45c3db6): postgres → `init` (bootstrap, migrate, org `demo`, two ingest keys at mode 644 for the read-only demo consumers) → `api` + `worker` (health checks) → `seed` → `web` on 127.0.0.1:8080. DEMO MODE (development profile, dev login, fixed demo secrets) is stated in the file header. `scripts/demo-{up,fault,down}.{sh,ps1}`.
* **Clean-state runs:** `down -v` then `up -d --build --wait`: exit 0 in 72 s (warm build cache). `demo-down.ps1` then `demo-up.ps1`: exit 0 in 27 s. Windows PowerShell 5.1 needed `$ErrorActionPreference = "Continue"`, because docker's stderr progress became terminating errors under redirection. `curl -fsS http://127.0.0.1:8080/auth/config` → `{"mode":"dev","dev_login":true}`. All four seeded investigations matched the benchmark through nginx. `E2E_BASE_URL=http://127.0.0.1:8080 npx playwright test` → 3 passed, three times against fresh or rebuilt stacks, under nginx's production CSP. `docker stats`: core ≈ 333–364 MiB.
* **Live path (demo profile, one run):** `demo-up.sh --live` exit 0 in 36 s. `init-demo` created the 3 integrations and set `default_source: live`; `up --wait` doesn't wait for this one-shot. After a ~3-minute baseline, `demo-fault.sh` at 09:25:23 set `catalog.error_rate=0.4`, and the flag service recorded the change in Aegis's change feed. Alertmanager delivered `HighErrorRate` for frontend (incident at 09:26:09) and catalog (09:26:19). Each was auto-investigated with live telemetry in about 1 s: metrics 12 series, traces ~1,750–1,800 spans (truncated), logs ~470–530 records, 1 change event.
  * **catalog incident → PLAUSIBLE / moderate, root cause `catalog`, mechanism `change_induced:feature_flag`.** The engine reported "no valid baseline was available" and suggested comparing with the previous day. The automatic window takes a 60-minute baseline ending 5 minutes before the alert, while the stack had been up about 5 minutes. That's why this run is PLAUSIBLE and not the CONFIRMED of the 2026-10-02 direct-collector run, which used a hand-picked 3-minute baseline. CONFIRMED was not attempted with an hour-long warm-up.
  * **frontend incident → INSUFFICIENT.** "Leading candidate(s) catalog could not be established as root cause."
  * Demo profile memory after ~10 minutes: ≈ 934 MiB across 15 containers.
* **Defect found and fixed:** running `demo-up --live` after `demo-up` re-ran `init`, so compose recreated `api` with a new IP. nginx had resolved `api` once at startup and returned 502 until restarted. Fix: `resolver 127.0.0.11` + `server api:8000 resolve` (nginx 1.29.8), plus `restart: true` on web's dependency on api. In a forced-IP-change experiment, nginx recovered without a restart (once after ~30 s, once immediately); before the fix it never recovered.

Does not show: accuracy (one live run); a CONFIRMED automatic live verdict; any non-demo deployment (DEMO MODE only); cold-cache build time (the images were partly cached).

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

Portfolio-release status (2026-11-09), matching the "Portfolio release scope" table in [`production-roadmap.md`](production-roadmap.md).

| Milestone | Status | Evidence |
|---|---|---|
| M0 audit, decisions, research preservation | DONE | roadmap; research suite 159 passed (CI); MIT licence 2026-11-09 |
| M1 pure RCA core + benchmark gate | DONE (LLM-free) | core tests; benchmark gate 15/15 in CI |
| M2 persistence, jobs, API, auth, tenancy, audit, ingestion | DONE | 134 PostgreSQL tests in CI with `AEGIS_REQUIRE_DB=1` (none skipped) |
| M3 LLM adjudication | PARTIAL | adapters, validator, policy, slots, records done; no live LLM output accepted (1B model); hosted models DEFERRED (stretch S1) |
| M4 live integrations | PARTIAL | Prometheus/Jaeger/Loki/OpenSearch + change feed; live demo run 2026-11-09 → PLAUSIBLE catalog; Tempo/Kubernetes DEFERRED |
| M5 web application | PARTIAL (portfolio scope met) | 78 unit + 3 Playwright E2E in CI against the compose stack; remediation UI DEFERRED (stretch S3) |
| M6 remediation and notifications | PARTIAL | remediation lifecycle tested; the two reconciler robustness gaps were fixed in Phase 6; notifications DEFERRED |
| M7 containers, compose stack, reproducible demo | DONE | one-command stack verified from a clean Docker state; E2E in CI |
| M8 self-observability, retention, MCP, topology | DEFERRED | cut by the 2026-11-08 re-scope (read-only MCP server = stretch S4) |
| M9 Kubernetes packaging and CI/CD | PARTIAL | GitHub Actions CI + gitleaks + Dependabot done; Helm/Kubernetes, SBOM, image scanning DEFERRED |
| M10 scale, chaos, benchmark breadth | DEFERRED | load/chaos and 25 scenario types cut; RCAEval = stretch S2 |
| M11 release candidate | DEFERRED | replaced by portfolio release v1.0.0 |
