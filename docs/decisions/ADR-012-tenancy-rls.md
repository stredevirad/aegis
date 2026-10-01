# Title: Tenant isolation with Row-Level Security plus application scoping
# Status: Accepted
# Date: 2026-10-01
# Source: production-readiness audit; Council A (docs/councils/council-A-architecture.md)

## Decision
* Every tenant-owned table has `org_id` and an RLS policy `org_id = current_setting('aegis.org_id')::uuid`, with `FORCE ROW LEVEL SECURITY`.
* Migrations run as the owning role; the application connects as `aegis_app`, which owns nothing and lacks `BYPASSRLS`.
* Each request/job transaction sets `SET LOCAL aegis.org_id` (transaction-scoped, so pooled connections cannot leak a tenant).
* Operations that must run before the organization is known (session lookup, API-key lookup, job claim) go through narrow `SECURITY DEFINER` functions that return only what is needed.
* Repositories also filter by `org_id` explicitly, and every repository and route has a cross-tenant leak test.

## Consequences
A forgotten `WHERE org_id` fails closed. Tests must run against real PostgreSQL (SQLite cannot emulate RLS).
