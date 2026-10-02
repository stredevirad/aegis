# Aegis — Database

PostgreSQL 16 holds all product state: tenants, identities, incidents, alerts, change events, investigations, jobs, events, snapshots, the evidence graph and the audit chain. The schema is defined by SQL migrations in `backend/aegis/db/migrations/versions/` (Alembic, one SQL file per revision); `backend/aegis/db/tables.py` only describes columns for query building and is checked against the migrated schema by `tests/db/test_schema_drift.py`.

## Roles (ADR-012)

| Role | Login | BYPASSRLS | Owns | Used by |
|---|---|---|---|---|
| `aegis_owner` | yes | no | schema `aegis`, all tables | migrations and the operator CLI (`aegis admin …`) |
| `aegis_app` | yes | **no** | nothing | API and worker |
| `aegis_definer` | no | yes | only the SECURITY DEFINER functions below | — (functions run as it) |

`python -m aegis.db.bootstrap` (or `aegis bootstrap-db`) creates the roles, the database and the schema as a superuser. It is idempotent and re-asserts role attributes on every run (a pre-existing `aegis_app` with `BYPASSRLS` is corrected). Passwords never pass through dollar-quoted SQL bodies; statements are produced by the server's `format('%L')`. `aegis_app` has a 15 s statement timeout and a 60 s idle-in-transaction timeout. `PUBLIC` has no rights on the schema or database; `aegis_app` cannot create objects anywhere (no TEMP, no CREATE).

## Tenancy

Every tenant-owned table has `org_id` and a policy `tenant_isolation`: `org_id = aegis.current_org()` for both `USING` and `WITH CHECK`, with `ENABLE` **and** `FORCE ROW LEVEL SECURITY`. `aegis.current_org()` is `NULLIF(current_setting('aegis.org_id', true), '')::uuid`, so a connection without a tenant setting sees **zero rows** (fail closed). The application sets the tenant per transaction only (`set_config(..., true)` in `aegis.db.engine.tenant_tx`); a source scan test forbids session-scoped settings. `organizations` is visible only as the current org; `users` only to themselves and to organizations they belong to.

Child tables reference their parents with **composite** foreign keys `(org_id, parent_id) → parent(org_id, id)`, so a row cannot point at another tenant's parent even if application code passed the wrong ID.

Pre-tenant operations go through narrow SECURITY DEFINER functions (owned by `aegis_definer`, `search_path` pinned, `EXECUTE` revoked from `PUBLIC`):

| Function | Granted to | Returns |
|---|---|---|
| `session_lookup(token_hash)` | `aegis_app` | session, org, user, *current* membership role, revoked, user disabled |
| `api_key_lookup(hash)` | `aegis_app` | key, org, prefix, scopes, expiry, revoked |
| `user_upsert(issuer, subject, email, verified, name)` | `aegis_app` | user id |
| `user_memberships(user)` | `aegis_app` | the user's organizations and roles |
| `invitations_for_email(email)` | `aegis_app` | open, unexpired invitations |
| `claim_job(worker, lease_s, org_cap)` | `aegis_app` | one claimable job (per-org running cap, priority, age) |
| `reap_dead_jobs()` | `aegis_app` | jobs whose lease expired with no attempts left (job → `dead`, investigation → `failed` + event, atomically) |
| `promote_deferred_jobs(queue_limit)` | `aegis_app` | deferred jobs moved to `queued` while their org is under its limit |
| `org_id_for_slug(slug)` | `aegis_owner` only | organization id (operator CLI) |
| `admin_set_user_disabled(email, disabled)` | `aegis_owner` only | (user, org) pairs; deletes sessions when disabling |

## Tables

| Table | Tenant | Purpose and key constraints |
|---|---|---|
| `organizations` | self | `slug` unique; `settings` JSON validated by `aegis.org_settings.OrgSettings` (auto-investigation, default source, grouping window, auto-resolve, remediation enablement) |
| `users` | global | `(issuer, subject)` unique; `disabled`; app role has SELECT only (writes via `user_upsert`) |
| `memberships` | yes | `(org_id, user_id)` PK; role ∈ admin/investigator/reviewer/viewer |
| `invitations` | yes | lower-case email; one open invitation per (org, email) (partial unique index); accepted only for verified emails |
| `sessions` | yes | `token_hash` = HMAC of the cookie token; idle and absolute expiry; revocation |
| `api_keys` | yes | `prefix` unique (display), `secret_hash` = HMAC of the full key (32 bytes); scopes ⊆ {ingest:alerts, ingest:changes, read}; mandatory expiry; rotation link; no DELETE grant |
| `incidents` | yes | severity, status lifecycle, service/environment, `group_key` (non-NULL = open for alert grouping; partial unique `(org_id, group_key)`), `alert_count`, `last_alert_at`, `auto_investigated_at` |
| `alerts` | yes | one row per occurrence `(org_id, fingerprint, starts_at)` unique; `starts_at` is the sender-reported start (the dedup identity); `receive_count`, `last_received_at`; `generator_url` must be http(s) |
| `change_events` | yes | deploy/config/flag/rollout/infrastructure/other; `(org_id, source, external_id)` unique (derived from content when not supplied); immutable for the app role |
| `investigations` | yes | explicit `window_*` and `baseline_*`; `source` JSON; verdict columns + `verdict` JSON (incl. decision trace); `cancel_requested`; `event_seq` counter; **one active (queued/running) investigation per incident** (partial unique index); `(org_id, idempotency_key)` unique |
| `investigation_jobs` | yes | status ∈ deferred/queued/running/succeeded/failed/dead/cancelled; attempts/max_attempts; lease owner/expiry; priority; one job per investigation |
| `investigation_events` | yes | `(investigation_id, seq)` unique; append-only for the app role |
| `snapshots` | yes | one per investigation; versioned payload, digest, synthetic flag, redaction record, `expires_at` for retention |
| `evidence`, `evidence_relations`, `candidates`, `root_causes` | yes | keyed by `(org_id, investigation_id, …)`; evidence is inserted, never upserted, so it cannot be re-homed across investigations or tenants (audit DATA-01) |
| `audit_events` | yes | hash chain per org (trigger assigns `seq`, `ts`, `prev_hash`, `hash`); app role has INSERT/SELECT only; `audit_verify()` returns the first broken sequence number |

## Concurrency rules

* **Lock order:** investigation row before job row (API cancel, worker, reaper). Audit inserts are the last lock a transaction takes (the chain trigger holds a per-org advisory lock until commit); code that would audit mid-transaction buffers (`aegis.audit.AuditBuffer`).
* **Ingestion:** one per-organization advisory lock as the first statement of each payload's transaction (overlapping-group payloads previously deadlocked against the audit lock; regression test `tests/api/test_ingest_concurrency.py`).
* **Job claims** serialise on one global advisory lock inside `claim_job` so the per-organization running cap cannot be exceeded by concurrent claimers.
* **Fencing:** every write a worker makes for a job includes `(status='running', lease_owner, attempts)`; a worker that lost its lease cannot commit results.

## Audit chain: what it does and does not protect

The chain detects modification by the application (which cannot UPDATE or DELETE) and accidental corruption. It is **not** tamper-evident against the database owner/operator, who can recompute it. Hashes are computed in SQL over UTC epoch microseconds and canonical `jsonb` text (independent of session time zone; tested).

## Migrations

| Revision | Content |
|---|---|
| 0001 | roles' grants, tenancy helpers, identity, incidents, investigations, jobs, events, snapshots, evidence graph, audit chain, RLS, definer functions |
| 0002 | API keys, invitations, alerts, change events, incident grouping, event sequence, deferred jobs and priority, lookups, atomic reaper |
| 0003 | readiness-probe grant on `alembic_version` |
| 0004 | operator-only definer functions for the CLI |

Migrations run as `aegis_owner`: `aegis migrate upgrade` (uses `AEGIS_MIGRATION_DATABASE_URL`). Every revision has a tested downgrade (`tests/db/test_tenancy_and_schema.py::test_migration_downgrade_and_upgrade_round_trip`). `/readyz` reports not-ready until the database is at the head revision shipped with the build.
