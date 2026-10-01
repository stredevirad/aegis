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

## Amendment 1 (Council C, 2026-10-01)
* Policies use `NULLIF(current_setting('aegis.org_id', true), '')::uuid` so a pooled connection without a setting yields zero rows rather than an error or a leak; a test asserts that a query without `SET LOCAL` returns nothing.
* Every `SECURITY DEFINER` function sets `search_path` explicitly; views are created with `security_invoker = true`.
* A CI catalog test asserts that every table with an `org_id` column has RLS enabled, forced, and at least one policy, and that `aegis_app` owns no table and lacks `BYPASSRLS`.
* Tests run as `aegis_app`, never as the superuser.
* Trust statement: RLS is defence in depth inside one deployment. Mutually hostile tenants should use separate deployments; the operator and database owner are trusted.
