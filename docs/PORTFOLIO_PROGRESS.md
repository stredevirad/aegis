# Portfolio release: progress

Plan source: the owner's master prompt (`.claude/MASTER_PROMPT.md`, gitignored). This file is the
resumable checklist. Tick an item only after running its command in this environment, and put
the evidence after the `—`. The verification ledger is `docs/production-readiness.md`.

## Phase 1: Consolidate, secure, first push
- [x] 1. Backup bundle + uncommitted patch + file copies; repo-local identity — `~/aegis-backups/aegis-2026-11-09.bundle` (verify OK), `uncommitted-2026-11-09.patch`, `worktree-uncommitted-2026-11-09.patch`, `files/` (3 migration + 2 RcaView files); identity = Joshua Herbert Japheth <joshuajapheth14@gmail.com>
- [x] 2. Baseline (2026-11-09, Windows 11, PG 16.15 container) — backend `check.sh -m "not live"`: ruff/mypy strict (109 files) clean, 352 passed / 4 deselected, benchmark gate OK; research suite: 159 passed / 6 deselected
- [x] 3. `.gitignore` additions; `.gitattributes` += `*.conf`, `.dockerignore` LF — 3d69820
- [x] 4. Commit Council-D backend follow-ups + `docs/councils/council-D-note.md` — 8b5fbad
- [ ] 5. Merge frontend branch, recover the 2 RcaView files, frontend gate (75 tests), remove worktree, delete branch
- [ ] 6. Rename `production/platform` → `main` (leave `master` + `feature/mcp-interoperability` local; both are ancestors)
- [ ] 7. `.mailmap`, MIT `LICENSE`, pyproject licence, roadmap M0 licence row → DONE
- [ ] 8. gitleaks over full history, triage, `.gitleaksignore`, clean re-run
- [ ] 9. Remote `origin` → `stredevirad/aegis` (must exist and be private); `gh` has the `workflow` scope
- [ ] 10. Push `main` + both research tags; verify private + default branch `main`
- [ ] 11. `CLAUDE.md`, this file finalised, council P1/P2 notes
- [ ] 12. Checkpoint tag `checkpoint-p1`, phase report

## Phase 2: Operator UI against the real backend
- [ ] 1. Dev stack running (compose `--profile app`, `dev-stack.sh`, Vite 5173)
- [ ] 2. Playwright dev dependency + config + `e2e/golden-path.spec.ts` (s06 golden path, s08 abstention, s10 prompt injection; fail on console errors / unexpected ≥400)
- [ ] 3. Fix the `/snapshot` 404 race (+Vitest); remove `RemediationPlaceholder`
- [ ] 4. `aegis admin seed-demo --org demo` (idempotent, tested): s06, s09, s10, s08
- [ ] 5. Light polish (≤1 h)
- [ ] 6. Gate: backend + frontend + Playwright green 3× in a row; servers stopped
- [ ] 7. Checkpoint `checkpoint-p2`

## Phase 3: One-command stack
- [ ] 1. `backend/Dockerfile` (pinned python/uv, non-root, scenarios) + `.dockerignore`
- [ ] 2. `deploy/compose.yaml` core services (postgres, init, api, worker, seed, web on :8080) + `create-api-key` CLI + demo-profile `init-demo` wiring (connectors, integrations, auto-investigate)
- [ ] 3. `scripts/demo-{up,fault,down}.{ps1,sh}`; `.env.example` placement
- [ ] 4. Clean-state verification: timings, `docker stats` memory, nginx `/auth/config`, Playwright against :8080
- [ ] 5. Live path (demo profile): fault → alert → incident → live investigation; record the result as observed
- [ ] 6. Checkpoint `checkpoint-p3`

## Phase 4: CI
- [ ] 0. `AEGIS_REQUIRE_DB=1` makes an unreachable DB fail instead of skip (+test)
- [ ] 1. `.github/workflows/ci.yml`: backend, research, frontend, images, secrets, e2e
- [ ] 2. `.github/dependabot.yml`
- [ ] 3. Push, watch, fix until green; record run URL + passed/skipped counts
- [ ] 4. Badges (if not deferred to Phase 5)
- [ ] 5. Checkpoint `checkpoint-p4`

## Phase 5: README, media, truthful docs, release
- [ ] 1. GIF + 4 screenshots in `docs/media/`
- [ ] 2. README rewrite (research README → `docs/research/README-research.md`)
- [ ] 3. Docs truthfulness pass (index, roadmap portfolio-scope section, research-era banners, demo script)
- [ ] 4. `private/INTERVIEW_PREP.md` (gitignored)
- [ ] 5. Independent stranger review (one Sonnet subagent), fixes, re-run quickstart
- [ ] 6. `gh release create v1.0.0` (repo stays private); final ledger snapshot
- [ ] 7. Checkpoint `checkpoint-p5`; final report; ask about stretch phases

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

## Facts that differ from the 2026-11-08 snapshot
- An extra local branch, `feature/mcp-interoperability`, points at dc7c032 (= `v1.1-research-freeze`), an ancestor of `main`. It stays local and unpushed.
- There are 23 placeholder `stredi` commits, not 22 (the frontend wip commit 762a9e9 is one of them).
- Baselines were re-run on Windows 11 on 2026-11-09 (not Linux).
