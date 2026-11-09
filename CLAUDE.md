# Aegis: notes for Claude Code

Aegis is an evidence-grounded incident investigation platform. A deterministic RCA engine names
the service that caused an incident, cites the metrics, logs and traces it relied on, and abstains
when the evidence is weak. An optional LLM may only select, explain or lower confidence.

**Current plan:** `docs/PORTFOLIO_PROGRESS.md` (resumable checklist; first unchecked item = next
step). Full brief: `.claude/MASTER_PROMPT.md` (gitignored, owner's machine only).
**Ledger:** `docs/production-readiness.md`. Record every verification there (command, result, date).

## Repo map
- `backend/aegis/`: production package (FastAPI API, worker, PostgreSQL queue, RCA core, collectors,
  egress, LLM adjudication, remediation, audit, CLI `aegis`). Tests in `backend/tests/`.
- `frontend/`: React 19 + TS 6 + Vite 8 operator UI (TanStack Query, React Router, Vitest, ESLint 10);
  nginx image via `frontend/Dockerfile`.
- `src/`, `tests/`, `scripts/`, `config/`: **frozen research layer** (tags `v1.0-research-complete`,
  `v1.1-research-freeze`). Don't change it.
- `eval/`: synthetic scenarios (`eval/scenarios`), hashed held-out set (`eval/heldout`), results.
- `deploy/`: `compose.dev.yaml` (test/dev Postgres, Dex), `compose.yaml` (demo telemetry profile).
- `demo/`: tiny instrumented demo services used by the demo profile.
- `docs/`: ledger, roadmap, architecture, ADRs (`docs/decisions/`), councils (`docs/councils/`).

## Gates (run before every commit that touches code)
- Backend (from `backend/`; needs compose.dev Postgres on 55432): `bash scripts/check.sh -m "not live"`
- DB tests only: `uv run --frozen pytest -m db -q`
- Benchmark: `uv run --frozen python -m aegis.benchmark.run --scenarios ../eval/scenarios --gate`
- Research (repo root): `uv run --no-project --with pydantic --with httpx --with pyyaml --with "mcp>=2.0.0" --with pytest --with-editable . python -m pytest tests -m "not integration" -q`
- Frontend (from `frontend/`): `npm run typecheck && npm run lint && npm test && npm run build`; E2E: `npx playwright test`

## Dev stack
- `docker compose -f deploy/compose.dev.yaml up -d --wait` (test PG :55432) · `--profile app` (dev PG :55433) · `--profile auth` (Dex :5556)
- `bash backend/scripts/dev-stack.sh`: API 127.0.0.1:8000 + worker; org `demo`; dev login `admin@example.com`
- `cd frontend && npm run dev`: 127.0.0.1:5173 (strict port; CSRF origins depend on it)
- Demo telemetry: `docker compose -f deploy/compose.yaml --profile demo up -d --build`
- Dev login works only with a development-like `AEGIS_PROFILE` and `AEGIS_AUTH_MODE=dev` (the profile
  defaults to production). Cookie-auth writes need `X-Aegis-CSRF: 1` plus an allowed `Origin`.

## Operating rules (condensed; the master prompt's rules 1–16 bind)
1. Truth: claim only what you ran here and saw pass; log it in the ledger. Code that exists isn't proof it's done.
2. Keep every honesty caveat: the synthetic benchmark is a regression gate, not an accuracy estimate;
   one live run is not accuracy; every live 1B-model output was rejected. Never inflate numbers.
3. Every README number needs a reproduce command next to it.
4. Never rewrite history: no filter-repo/filter-branch, rebase of existing commits, amend of pushed
   commits, force-push or tag deletion. Attribution is fixed via `.mailmap` only.
5. Never change repo visibility/settings or touch any repo but `stredevirad/aegis`. Going public is
   the owner's manual step.
6. Delete only files you created or that the plan names. Back up to `~/aegis-backups/` first.
7. Secrets: never print, commit or log credentials. A real secret in history means STOP and ask
   the owner. Dev fixtures go in `.gitleaksignore` with a comment.
8. LLM keys come only from env vars the owner sets; never write them to files.
9. No features outside the current phase, no taste refactors, no dependency upgrades.
10. Subagents only where a phase says so (`model: sonnet`, narrow task). No llm-council unless asked.
11. Prefer CLIs (`gh`, `git`, `docker`, `uv`, `npm`, `curl`). Check local `.d.ts`/venv source before docs.
12. Read large files in slices; rely on this file + PORTFOLIO_PROGRESS.md for continuity.
13. Time-boxes are real: on expiry, commit what works, write DEFERRED + findings, move on.
14. Line endings: `.gitattributes` forces LF for `*.sh`, Dockerfiles, YAML, `*.conf`, `.dockerignore`
    (CRLF for `*.ps1`). New bash scripts LF, PowerShell CRLF. Never mass-renormalize.
15. Ports bind to 127.0.0.1. Stop background servers between phases.
16. Start each phase with a short plan; end with the phase report; ask one precise question when blocked.

## Checkpoint routine
Gate green → tick PORTFOLIO_PROGRESS.md (+ ledger at phase end) → `git add <explicit paths>` →
conventional commit with the Claude co-author trailer → `git push origin main` → at phase end, an
annotated tag `checkpoint-p<N>` pushed → `git status -sb` shows nothing ahead.

## Windows notes
- Shell is Git Bash on Windows 11 (16 GB RAM). Keep Docker under ~8 GB. Never run the demo
  telemetry profile, a browser, Vite and the full test suite at the same time.
- `core.autocrlf=true`: working-tree text files may be CRLF; the index stays LF.
- Docker volume mounts of the cwd: `MSYS_NO_PATHCONV=1 docker run -v "$(pwd -W):/repo" …`.
- Docker Desktop is not auto-started (`C:\Program Files\Docker\Docker\Docker Desktop.exe`). On
  start, the old OpenTelemetry Demo containers (`ghcr.io/open-telemetry/*`) restart; stop them.
- Orphaned servers: `netstat -ano | grep -E ":(8000|5173) "`, then `taskkill //F //T //PID <pid>`
  (only PIDs you started).
- Local branches `master` and `feature/mcp-interoperability` are research-era ancestors of `main`
  and are intentionally not pushed.
