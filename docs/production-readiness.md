# Aegis — Production Readiness (verification ledger)

This is the engineering control document behind [`production-roadmap.md`](production-roadmap.md). A row is `VERIFIED` only when the command in the row was executed and its output is recorded here, with the date. `PARTIAL` states what works and what does not; `BLOCKED` names the external dependency; `PLANNED` means not implemented. Nothing here is inferred from documentation.

Environment for all runs unless stated: Windows 11, Python 3.13 (backend via `uv`, Python ≥ 3.12), PostgreSQL 16.15 from `deploy/compose.dev.yaml`, Docker Desktop 28.3.

## Current verification snapshot

| Suite | Command (from) | Result | Date |
|---|---|---|---|
| Backend unit + core + benchmark tests | `uv run --frozen pytest -m "not db and not live"` (`backend/`) | 59 passed | 2026-10-02 |
| Backend PostgreSQL tests (run as `aegis_app`) | `docker compose -f deploy/compose.dev.yaml up -d --wait` then `uv run --frozen pytest -m db` (`backend/`) | 21 passed | 2026-10-02 |
| Synthetic benchmark gate | `uv run --frozen python -m aegis.benchmark.run --scenarios ../eval/scenarios --gate` (`backend/`) | 15/15 (12 correct, 3 appropriate abstentions), 0 confident-wrong, exit 0 | 2026-10-02 |
| Research suite (frozen layer) | `uv run --no-project --with pydantic --with httpx --with pyyaml --with "mcp>=2.0.0" --with pytest --with-editable . python -m pytest tests -m "not integration"` (repo root) | 159 passed, 6 integration tests deselected | 2026-10-02 |
| Format / lint / types | `uv run --frozen ruff format --check .`, `ruff check .`, `mypy` (`backend/`) | clean | 2026-10-02 |

## Verification log

Newest first. Each entry: what was run, the observed result, and what it does not show.

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
| M0 audit, decisions, research preservation | DONE | roadmap; research suite 159 passed |
| M1 pure RCA core + benchmark gate | DONE (LLM-free) | core tests; benchmark gate |
| M2 persistence, jobs, API, auth, tenancy, audit, ingestion | IN PROGRESS | schema 0001 + RLS + audit chain + claim function verified; everything else PLANNED |
| M3–M11 | PLANNED | — |
