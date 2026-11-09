# Portfolio release: progress

Plan source: the owner's master prompt (`.claude/MASTER_PROMPT.md`, gitignored). This file is the
resumable checklist. Tick an item only after running its command in this environment, and put
the evidence after the `—`. The verification ledger is `docs/production-readiness.md`.

## Phase 1: Consolidate, secure, first push
- [x] 1. Backup bundle + uncommitted patch + file copies; repo-local identity — `~/aegis-backups/aegis-2026-11-09.bundle` (verify OK), `uncommitted-2026-11-09.patch`, `worktree-uncommitted-2026-11-09.patch`, `files/` (3 migration + 2 RcaView files); identity = Joshua Herbert Japheth <joshuajapheth14@gmail.com>
- [x] 2. Baseline (2026-11-09, Windows 11, PG 16.15 container) — backend `check.sh -m "not live"`: ruff/mypy strict (109 files) clean, 352 passed / 4 deselected, benchmark gate OK; research suite: 159 passed / 6 deselected; DB subset `pytest -m "db and not live"`: 128 passed
- [x] 3. `.gitignore` additions; `.gitattributes` += `*.conf`, `.dockerignore` LF — 3d69820
- [x] 4. Commit Council-D backend follow-ups + `docs/councils/council-D-note.md` — 8b5fbad
- [x] 5. Merge frontend branch, recover the 2 RcaView files, frontend gate, remove worktree, delete branch — merge 4d3afe5 (conflict-free), files `cmp`-identical to backup, 20ec99a; `npm ci`, tsc + ESLint clean, Vitest 75 passed in 7 files, build OK (444.81 kB JS); `git worktree remove` OK; `git branch -d` OK (merged)
- [x] 6. Rename `production/platform` → `main` (`master` + `feature/mcp-interoperability` stay local; both are ancestors) — `git branch -vv`
- [x] 7. `.mailmap`, MIT `LICENSE`, pyproject licence, roadmap M0 licence row → DONE — f47fd72; `git shortlog -sne --all` shows 1 identity (59 commits); `uv lock --check` + `uv sync --frozen` OK
- [x] 8. gitleaks over full history, triage, `.gitleaksignore`, clean re-run — gitleaks v8.30.1, 59 commits (all refs): 3 findings, all test fixtures (fake bearer token, jwt.io sample JWT, 20-char dummy PEM); allowlisted in 20fe630; re-run: "no leaks found"
- [x] 9. Remote `origin` → `stredevirad/aegis`; `gh` scopes include `workflow` — repo existed, PRIVATE, empty
- [x] 10. Push `main` + both research tags; verify — `gh repo view`: `{"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE"}`; `git status -sb`: `## main...origin/main`
- [x] 11. `CLAUDE.md`, this file finalised, council P1/P2 notes
- [x] 12. Checkpoint tag `checkpoint-p1` (annotated, on the commit that adds this line), phase report

## Phase 2: Operator UI against the real backend
- [x] 1. Dev stack running (compose `--profile app`, `dev-stack.sh`, Vite 5173) — Vite bound only `[::1]` on Windows; pinned `host: "127.0.0.1"` in `vite.config.ts`
- [x] 2. Playwright dev dependency + config + `e2e/golden-path.spec.ts` (s06 golden path, s08 abstention, s10 prompt injection; fail on console errors / unexpected ≥400) — 6bd3693; @playwright/test 1.65.0 + @types/node 24 (`tsconfig.e2e.json`); first run reproduced the `/snapshot` 404 in all 3 tests
- [x] 3. Fix the `/snapshot` 404 race (+Vitest); remove `RemediationPlaceholder` — 9b11dc4; query waits for `completed` or the replayed `snapshot_stored` event; 3 Vitest tests (mutation-checked: the first fails without the gate); placeholder component, usages and CSS deleted; no replacement sentence (a pointer to repo docs doesn't read naturally in an operator UI)
- [x] 4. `aegis admin seed-demo --org demo` (idempotent, tested): s06, s09, s10, s08 — `backend/aegis/demo_seed.py`, `tests/db/test_demo_seed.py` 4 passed (idempotency, CLI entry point, unknown org/scenarios, worker reaches s06 CONFIRMED/strong, s09+s10 PLAUSIBLE/moderate on catalog, s08 INSUFFICIENT with next checks); `dev-stack.sh` now seeds; same verdicts observed on the running dev stack
- [x] 5. Light polish (≤1 h) — screenshots of dashboard, incidents and incident pages at 1366×900 reviewed: no layout bugs; seeded description no longer shows literal backticks
- [x] 6. Gate: backend + frontend + Playwright green 3× in a row; servers stopped — backend 356 passed / 4 deselected; frontend 78 passed in 8 files, build OK; Playwright 3 passed ×3 (13.3/14.8/15.4 s); dev-stack and Vite stopped (orphaned uv/aegis/python/node trees killed by PID; ports 8000/5173 free)
- [x] 7. Checkpoint `checkpoint-p2`

## Phase 3: One-command stack
- [x] 1. `backend/Dockerfile` (pinned python/uv, non-root, scenarios) + `.dockerignore` — ffef2ac; python:3.13.16-slim + uv 0.12.15 (both verified with `docker manifest inspect`); 338 MB; uid 10001; scenarios via named build context `scenarios`
- [x] 2. `deploy/compose.yaml` core services (postgres, init, api, worker, seed, web on :8080) + `create-api-key` CLI + demo-profile `init-demo` wiring (connectors, integrations, auto-investigate) — ffef2ac, 45c3db6; `init-demo` (API script) created the 3 integrations and set `default_source: live`. Note: `up --wait` does not wait for `init-demo` (one-shot, no health check); it finishes a few seconds after
- [x] 3. `scripts/demo-{up,fault,down}.{ps1,sh}`; `.env.example` placement — 45c3db6; PS 5.1 needs `$ErrorActionPreference = "Continue"` around docker (stderr progress became terminating errors when redirected); root `.env.example` kept (referenced by research docs), `deploy/.env.example` added
- [x] 4. Clean-state verification: timings, `docker stats` memory, nginx `/auth/config`, Playwright against :8080 — `down -v` → `up --build --wait` exit 0 in 72 s (bash, warm build cache) and `demo-down.ps1` → `demo-up.ps1` exit 0 in 27 s; `curl /auth/config` → `{"mode":"dev","dev_login":true}`; seeded verdicts match; Playwright 3 passed against :8080 (twice); core memory ≈ 0.33–0.36 GB; with demo profile ≈ 0.74 GB at start; key files 0644 uid 10001
- [x] 5. Live path (demo profile): fault → alert → incident → live investigation; record the result as observed — PARTIAL vs the hoped-for CONFIRMED: `demo-up.sh --live` (36 s) → 3-min wait → `demo-fault.sh` 09:25:23 → Alertmanager → 2 incidents (catalog 09:26:19, frontend 09:26:09) → 2 automatic live investigations, ~1 s each: catalog → **PLAUSIBLE/moderate, root cause `catalog`, `change_induced:feature_flag`** (reason: "no valid baseline was available": the automatic window uses a 60-min baseline, but the stack was ~5 min old); frontend → INSUFFICIENT (catalog the leading candidate, not established). Found and fixed: nginx kept proxying to a recreated `api` (502) → `resolve` + `web` restart-on-api
- [x] 6. Checkpoint `checkpoint-p3`

## Phase 4: CI
- [x] 0. `AEGIS_REQUIRE_DB=1` makes an unreachable DB fail instead of skip (+test) — 98cdce1~1; with Postgres stopped: default 3 skipped, `AEGIS_REQUIRE_DB=1` 3 errors; local gate with it: 360 passed
- [x] 1. `.github/workflows/ci.yml`: backend, research, frontend, images, secrets, e2e — 98cdce1; first run failed only on `astral-sh/setup-uv@v10` (no such tag; pinned v10.3.3, d211838)
- [x] 2. `.github/dependabot.yml` — actions, npm, uv, docker; weekly, grouped, ≤2 open PRs each (first scans started on push)
- [x] 3. Push, watch, fix until green; record run URL + passed/skipped counts — https://github.com/stredevirad/aegis/actions/runs/42594753903 (d211838): all 6 jobs green; backend 360 passed / 4 deselected / 0 skipped (134 of them PostgreSQL tests), research 159 passed, frontend 78 passed, E2E 3 passed, gitleaks 73 commits no leaks
- [x] 4. Badges — deferred to the Phase 5 README rewrite (as the plan allows)
- [x] 5. Checkpoint `checkpoint-p4`

## Phase 5: README, media, truthful docs, release
- [x] 1. GIF + 4 screenshots in `docs/media/` — f05d9ef; `e2e/media.spec.ts` (E2E_MEDIA=1) + golden path with E2E_RECORD=1 → ffmpeg (10 fps, 1100 px, 5.0 MB); found and fixed: abstentions were labelled "VERIFIED FINDING" (b5df992)
- [x] 2. README rewrite (research README → `docs/research/README-research.md`) — b1e402d; all relative links checked to exist; numbers re-run 2026-11-09 (benchmark, held-out)
- [x] 3. Docs truthfulness pass (index, roadmap portfolio-scope section, research-era banners, demo script) — 9c6d5fa; 15 research-era docs bannered; ledger milestone table matches the roadmap
- [x] 4. `private/INTERVIEW_PREP.md` (gitignored) — `git check-ignore` confirms; never committed
- [x] 5. Independent stranger review (one Sonnet subagent), fixes, re-run quickstart — fresh `gh repo clone` + README quickstart: no broken steps, no dead links (35 relative), all numbers matched (benchmarks, seeded verdicts, 62 egress tests, E2E 3 passed). Nits fixed: s06 "each with cited evidence" overstated (2 passing checks carry no evidence IDs), "delete everything" (images are kept), memory figure labelled a recommendation, CI row notes docs-only pushes skip CI. Re-run by me: `demo-up.sh` exit 0 in 36 s, login OK, 4 seeded verdicts as documented
- [x] 6. `gh release create v1.0.0` (repo stays private); final ledger snapshot
- [x] 7. Checkpoint `checkpoint-p5`; final report; ask about stretch phases

## Phase 6: Final fixes and polish (closing phase)
The earlier tutor-mode plan (owner writes the fixes) was cancelled by the owner on 2026-11-09; Claude does every item.
- [x] 6.1 Remediation lost update: regression test (fails first), then compare-and-set final writes in `execute()`/`rollback()`; 0 rows → keep the reconciled row, audit the late outcome, return the current row — 2 new tests failed first for the right reason (`'verifying' == 'failed'`, `'rolled_back' == 'rollback_failed'`); after the fix (`_finish` CAS + `_keep_reconciled`, audit phase `finished_late`): `test_remediation.py` 12 passed; `check.sh -m "not live"` 362 passed / 4 deselected, benchmark gate OK
- [x] 6.2 Maintenance-loop fault isolation: tests, per-row `except Exception` in `reconcile_stuck()` (missing row handled), `except Exception` in `Worker._maintenance()` (never `BaseException`); README Limitations + Open follow-ups updated — 2 new tests failed first with the `RuntimeError` escaping (`reconcile_stuck`, `_maintenance`); a third guards that cancellation still stops the loop (passes before and after); after the fix: `check.sh -m "not live"` 365 passed / 4 deselected, benchmark gate OK. README bullet + Limitations, ledger M6 row and roadmap M6 row updated
- [x] 6.3 `.github/dependabot.yml`: `open-pull-requests-limit: 0` for every ecosystem — PyYAML parse: github-actions, npm, uv, docker all 0
- [x] 6.4 `core/decide.py`: render modality lists as plain text, not a Python list repr — new test (fails on the old text); benchmark and held-out output byte-identical before/after (12 CORRECT + 3 ABSTAINED; held-out 6 abstained + 2 partially correct, 0 confident-wrong); `git diff --stat -- eval/` empty; `check.sh` 366 passed / 4 deselected. Other check reasons still use list reprs (e.g. "dependencies ['catalog'] are also anomalous"); left unchanged per the item's scope
- [x] 6.5 `RcaView.tsx` VerdictSummary: readable headline from structured root causes (`mechanismLabel()`), Vitest test — headline/summary for CONFIRMED/PLAUSIBLE worded from `root_causes` (e.g. "Strongly supported: catalog — Change-induced (feature flag)"); other verdicts and the backend `display_label` unchanged; 3 new Vitest tests (2 fail on the old component); typecheck + ESLint clean, Vitest 81 passed in 8 files (incl. `hostile.test.tsx`), build OK (444.88 kB JS)
- [x] 6.6 `ci.yml`: drop "(private-repo Actions minutes)" from the paths-ignore comment — `grep -rn private-repo .github` finds nothing; YAML parses
- [x] 6.7 README: plain-language sentence, results lead lines, frozen-research line; links and numbers verified — 41 relative links resolve; `aegis.benchmark.run`: 12 CORRECT + 3 ABSTAINED_APPROPRIATELY, 0 confident-wrong; `aegis.benchmark.heldout`: 6 ABSTAINED_APPROPRIATELY + 2 PARTIALLY_CORRECT, 0 confident-wrong; CI row updated to the local counts (backend 366 / 137 PostgreSQL, frontend 81), to be confirmed by the 6.11 CI run
- [x] 6.8 Media: screenshots + reproducible GIF (env-gated spec + ffmpeg) from a fresh demo stack — `demo-down.sh` → `demo-up.sh` (both images rebuilt, 37 s); `e2e/demo-gif.spec.ts` (`E2E_GIF=1`): one video, cut by ffmpeg to the loaded s06 verdict (3.0 s), one citation click (marked by a ring; videos have no pointer), the loaded s08 abstention (3.0 s) → 9.8 s, 3.53 MB, 1100×725 (ffprobe). First attempt kept a blank + "Loading your session" frame at the splice (the video clock lags the wall clock); fixed with a 1.5 s pad after each end mark. Frame check: first frame = s06 verdict with the new headline, last frame = s08 abstention; ffmpeg scene scan shows changes only at the click and the splice. Screenshots regenerated (`E2E_MEDIA=1`, now waits for the seed); unused `dashboard.png`/`evidence-explorer.png` deleted (backed up to `~/aegis-backups/phase6-media/`)
- [x] 6.9 `private/INTERVIEW_PREP.md` (gitignored): anchors, both bugs explained, honest authorship answer — every `file:line` compared at b3bef0f vs HEAD: only `remediation/service.py` moved (+4: 256→260, 327→331, 439-444→443-448); new anchors for `_finish`, `_keep_reconciled`, `reconcile_stuck`, `_maintenance` and the tests; test counts 366/137; `git status` does not list it (ignored by `.gitignore:55`)
- [x] 6.10 Every CLAUDE.md gate incl. Playwright against the stack; Phase 6 ledger entry — backend `check.sh` 366 passed / 4 deselected; `pytest -m db` 137 passed / 2 skipped (db+live) / 231 deselected; benchmark gate OK (12 + 3, 0 confident-wrong); research 159 passed; frontend 81 passed + build; Playwright vs `demo-up.sh` stack 3 passed / 2 skipped (env-gated); stack stopped; ledger entry + snapshot rows updated
- [ ] 6.11 Push, CI green, tags `v1.0.1` + `checkpoint-p6`, GitHub release v1.0.1
- [ ] 6.12 GitHub housekeeping: close Dependabot PRs #1–#3, topics, security settings, main ruleset

## Deferred (cut by the 2026-11-08 portfolio re-scope; not built)
- Helm / Kubernetes deployment (M9): Docker Compose is enough for a one-command portfolio demo.
- SBOM, image scanning, supply-chain hardening beyond gitleaks + Dependabot: cost/benefit too low at portfolio scale.
- Load tests, chaos and failure-injection campaigns (M10): need sustained infrastructure; out of time-box.
- 25 scenario types: 15 synthetic scenarios + 8 held-out real captures already exercise the decision list.
- Council E / M11 war-room: superseded by the portfolio re-scope councils (P1, P2).
- Tempo and Kubernetes collectors: Prometheus, Loki/OpenSearch and Jaeger cover the demo.
- Slack / PagerDuty / webhook notifications: not needed to demonstrate RCA.
- LLM cost / price accounting: no paid provider is in the demo path.
- Live OpenAI / Anthropic runs: need owner-held keys; stretch S1 covers LLM evaluation.
- Retention sweeps: no long-lived deployment.
- Topology graph: not needed to show cited RCA.
- Production MCP client hardening: the research MCP layer stays frozen; stretch S4 offers a read-only server instead.
- Hosted / static demo site: the one-command local stack is the demo.
- Remediation UI: the backend lifecycle stays and is documented; the UI is stretch S3.

## Stretch (awaiting owner: "go S<n>")
- S1: LLM adjudication evaluation
- S2: public benchmark (RCAEval RE2-OB)
- S3: minimal two-person remediation panel
- S4: read-only Aegis MCP server

## Post-review fixes (2026-11-09, final Phase 5 commit)
- README / demo script: the s10 claim now says injected instructions are treated as data and the verdict still names `catalog` in that scenario, not that injection "can't change the verdict" (forged telemetry can mislead the checks; see Limitations). The v1.0.0 tag keeps the older wording; `main` is corrected.
- Windows quickstart: `powershell -ExecutionPolicy Bypass -File scripts\demo-up.ps1`. The default client policy (Restricted) blocks unsigned scripts; this machine has CurrentUser RemoteSigned.
- CLAUDE.md repo map: `scripts/demo-*` are product scripts; the other `scripts/*` are frozen research.
- Root `.env.example` marked research-era; it points to `deploy/.env.example`.
- Live-demo memory wording aligned with the measurement (~1 GB in total).

## Open follow-ups (not in any phase; owner decides)
- None. The two remediation findings from the 2026-11-09 automated security review of 8b5fbad (lost update on the final execute/rollback write; maintenance-loop fault isolation) were fixed in Phase 6 (6.1, 6.2).

## Facts that differ from the 2026-11-08 snapshot
- An extra local branch, `feature/mcp-interoperability`, points at dc7c032 (= `v1.1-research-freeze`), an ancestor of `main`. It stays local and unpushed.
- There are 23 placeholder `stredi` commits, not 22 (the frontend wip commit 762a9e9 is one of them).
- Baselines were re-run on Windows 11 on 2026-11-09 (not Linux).
