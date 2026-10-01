"""Database-level security tests (ADR-012, Council C): run as aegis_app against real PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from aegis.db import migrate
from aegis.db.engine import system_tx, tenant_tx

pytestmark = pytest.mark.db

NOW = datetime.now(UTC)


async def _incident(engine, org) -> uuid.UUID:
    async with tenant_tx(engine, org) as c:
        return (
            await c.execute(
                text(
                    "INSERT INTO incidents (org_id, title, severity, started_at) "
                    "VALUES (:o, 'checkout errors', 'high', :t) RETURNING id"
                ),
                {"o": org, "t": NOW},
            )
        ).scalar_one()


async def _investigation(engine, org, incident) -> tuple[uuid.UUID, uuid.UUID]:
    async with tenant_tx(engine, org) as c:
        inv = (
            await c.execute(
                text(
                    "INSERT INTO investigations (org_id, incident_id, window_start, window_end, "
                    "baseline_start, baseline_end, source) VALUES (:o, :i, :ws, :we, :bs, :be, '{}') "
                    "RETURNING id"
                ),
                {
                    "o": org,
                    "i": incident,
                    "ws": NOW - timedelta(minutes=20),
                    "we": NOW,
                    "bs": NOW - timedelta(minutes=80),
                    "be": NOW - timedelta(minutes=20),
                },
            )
        ).scalar_one()
        job = (
            await c.execute(
                text(
                    "INSERT INTO investigation_jobs (org_id, investigation_id) VALUES (:o, :i) RETURNING id"
                ),
                {"o": org, "i": inv},
            )
        ).scalar_one()
    return inv, job


# --- catalog ---------------------------------------------------------------------------


async def test_every_org_table_has_forced_rls_and_a_policy(owner_conn: asyncpg.Connection):
    rows = await owner_conn.fetch(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'aegis' AND c.relkind = 'r'
           AND (EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                         AND a.attname = 'org_id' AND NOT a.attisdropped)
                OR c.relname IN ('organizations', 'users'))
        """
    )
    assert len(rows) >= 13
    bad = [
        r["relname"]
        for r in rows
        if not (r["relrowsecurity"] and r["relforcerowsecurity"] and r["policies"] > 0)
    ]
    assert not bad, f"tables without enabled+forced RLS and a policy: {bad}"


async def test_app_role_owns_nothing_and_cannot_bypass_rls(owner_conn: asyncpg.Connection):
    role = await owner_conn.fetchrow(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = 'aegis_app'"
    )
    assert not any(role.values())
    owned = await owner_conn.fetchval(
        "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = 'aegis_app'"
    )
    assert owned == 0


async def test_definer_functions_pin_search_path_and_deny_public(owner_conn: asyncpg.Connection):
    rows = await owner_conn.fetch(
        """
        SELECT p.proname, p.proconfig, r.rolname AS owner,
               has_function_privilege('public', p.oid, 'EXECUTE') AS public_exec
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
          JOIN pg_roles r ON r.oid = p.proowner
         WHERE n.nspname = 'aegis' AND p.prosecdef
        """
    )
    assert {r["proname"] for r in rows} >= {"session_lookup", "claim_job", "user_upsert"}
    for r in rows:
        assert r["owner"] == "aegis_definer", r["proname"]
        assert any(c.startswith("search_path=") for c in (r["proconfig"] or [])), r["proname"]
        assert not r["public_exec"], r["proname"]


async def test_views_use_security_invoker(owner_conn: asyncpg.Connection):
    rows = await owner_conn.fetch(
        """
        SELECT c.relname, c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'aegis' AND c.relkind = 'v'
        """
    )
    bad = [r["relname"] for r in rows if "security_invoker=true" not in (r["reloptions"] or [])]
    assert not bad


# --- isolation behaviour -----------------------------------------------------------------


async def test_no_tenant_set_returns_zero_rows(app_engine, two_orgs):
    await _incident(app_engine, two_orgs["a"])
    async with system_tx(app_engine) as c:
        assert (await c.execute(text("SELECT count(*) FROM incidents"))).scalar_one() == 0
        assert (await c.execute(text("SELECT count(*) FROM organizations"))).scalar_one() == 0
    async with app_engine.begin() as c:  # not even set_config: still nothing
        assert (await c.execute(text("SELECT count(*) FROM incidents"))).scalar_one() == 0


async def test_tenant_a_cannot_read_or_write_tenant_b(app_engine, two_orgs):
    b_incident = await _incident(app_engine, two_orgs["b"])
    async with tenant_tx(app_engine, two_orgs["a"]) as c:
        assert (
            await c.execute(text("SELECT count(*) FROM incidents WHERE id = :i"), {"i": b_incident})
        ).scalar_one() == 0
        updated = await c.execute(
            text("UPDATE incidents SET title = 'pwned' WHERE id = :i"), {"i": b_incident}
        )
        assert updated.rowcount == 0
    with pytest.raises(DBAPIError):  # WITH CHECK refuses rows for another tenant
        async with tenant_tx(app_engine, two_orgs["a"]) as c:
            await c.execute(
                text(
                    "INSERT INTO incidents (org_id, title, severity, started_at) "
                    "VALUES (:o, 'x', 'low', now())"
                ),
                {"o": two_orgs["b"]},
            )


async def test_child_rows_cannot_reference_another_tenants_parent(app_engine, two_orgs):
    b_inc = await _incident(app_engine, two_orgs["b"])
    b_inv, _ = await _investigation(app_engine, two_orgs["b"], b_inc)
    with pytest.raises(DBAPIError):
        async with tenant_tx(app_engine, two_orgs["a"]) as c:
            await c.execute(
                text(
                    "INSERT INTO evidence (org_id, investigation_id, evidence_id, kind, summary) "
                    "VALUES (:o, :i, 'ev-x', 'change', 's')"
                ),
                {"o": two_orgs["a"], "i": b_inv},
            )


async def test_users_of_other_tenants_are_invisible(app_engine, su_conn, two_orgs):
    a_user = await su_conn.fetchval(
        "INSERT INTO aegis.users (issuer, subject, email) VALUES ('i', $1, 'a@example.com') RETURNING id",
        f"a-{uuid.uuid4()}",
    )
    b_user = await su_conn.fetchval(
        "INSERT INTO aegis.users (issuer, subject, email) VALUES ('i', $1, 'b@example.com') RETURNING id",
        f"b-{uuid.uuid4()}",
    )
    await su_conn.execute(
        "INSERT INTO aegis.memberships (org_id, user_id, role) VALUES ($1, $2, 'viewer'), ($3, $4, 'viewer')",
        two_orgs["a"],
        a_user,
        two_orgs["b"],
        b_user,
    )
    async with tenant_tx(app_engine, two_orgs["a"], a_user) as c:
        ids = {r[0] for r in (await c.execute(text("SELECT id FROM users"))).all()}
    assert a_user in ids and b_user not in ids


# --- audit -----------------------------------------------------------------------------------


async def test_audit_is_append_only_and_chain_verifies(app_engine, owner_conn, two_orgs):
    org = two_orgs["a"]
    async with tenant_tx(app_engine, org) as c:
        for i in range(5):
            await c.execute(
                text(
                    "INSERT INTO audit_events (org_id, actor_type, actor_id, action, details) "
                    "VALUES (:o, 'system', 'test', 'test.event', CAST(:d AS jsonb))"
                ),
                {"o": org, "d": f'{{"i": {i}, "note": "x\\u2028y"}}'},
            )
        assert (await c.execute(text("SELECT audit_verify()"))).scalar_one() is None
    for stmt in ("UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events"):
        with pytest.raises(ProgrammingError):  # permission denied for the app role
            async with tenant_tx(app_engine, org) as c:
                await c.execute(text(stmt))
    # The owner CAN tamper (documented trust boundary); the chain detects it. The tampering
    # is rolled back explicitly so later tests see an intact chain.
    tx = owner_conn.transaction()
    await tx.start()
    try:
        await owner_conn.execute("SELECT set_config('aegis.org_id', $1, true)", str(org))
        await owner_conn.execute(
            "UPDATE aegis.audit_events SET action = 'tampered' WHERE seq = 3 AND org_id = $1", org
        )
        assert await owner_conn.fetchval("SELECT aegis.audit_verify()") == 3
    finally:
        await tx.rollback()
    async with tenant_tx(app_engine, org) as c:
        assert (await c.execute(text("SELECT audit_verify()"))).scalar_one() is None


async def test_audit_hash_independent_of_session_timezone(app_engine, two_orgs):
    org = two_orgs["b"]
    async with tenant_tx(app_engine, org) as c:
        await c.execute(text("SET LOCAL TimeZone = 'Asia/Kolkata'"))
        await c.execute(
            text(
                "INSERT INTO audit_events (org_id, actor_type, action) VALUES (:o, 'system', 'tz.test')"
            ),
            {"o": org},
        )
    async with tenant_tx(app_engine, org) as c:
        await c.execute(text("SET LOCAL TimeZone = 'America/Los_Angeles'"))
        assert (await c.execute(text("SELECT audit_verify()"))).scalar_one() is None


# --- jobs ---------------------------------------------------------------------------------------


async def test_claim_works_under_forced_rls_without_tenant(app_engine, two_orgs, empty_queue):
    inc = await _incident(app_engine, two_orgs["a"])
    inv, job = await _investigation(app_engine, two_orgs["a"], inc)
    async with system_tx(app_engine) as c:
        rows = (await c.execute(text("SELECT * FROM claim_job('w1', 60, 10)"))).all()
    assert [(r.job_id, r.org_id, r.investigation_id, r.attempt) for r in rows] == [
        (job, two_orgs["a"], inv, 1)
    ]
    async with tenant_tx(app_engine, two_orgs["a"]) as c:
        status = (
            await c.execute(
                text("SELECT status, lease_owner, attempts FROM investigation_jobs WHERE id = :j"),
                {"j": job},
            )
        ).one()
    assert (status.status, status.lease_owner, status.attempts) == ("running", "w1", 1)
    async with system_tx(app_engine) as c:  # nothing else is claimable
        assert (await c.execute(text("SELECT * FROM claim_job('w2', 60, 10)"))).all() == []


async def test_concurrent_claimers_respect_per_org_cap(database, app_engine, su_conn, empty_queue):
    org = await su_conn.fetchval(
        "INSERT INTO aegis.organizations (slug, name) VALUES ($1, 'cap') RETURNING id",
        f"cap-{uuid.uuid4().hex[:8]}",
    )
    for _ in range(8):
        inc = await _incident(app_engine, org)
        await _investigation(app_engine, org, inc)

    url = database["app"].replace("+asyncpg", "")

    async def claimer(n: int) -> int:
        conn = await asyncpg.connect(url)
        try:
            got = 0
            for _ in range(3):
                async with conn.transaction():
                    row = await conn.fetchrow("SELECT * FROM aegis.claim_job($1, 300, 2)", f"w{n}")
                got += 1 if row else 0
            return got
        finally:
            await conn.close()

    results = await asyncio.gather(*(claimer(n) for n in range(6)))
    assert sum(results) == 2, results  # cap of 2 running jobs for this org, never exceeded
    running = await su_conn.fetchval(
        "SELECT count(*) FROM aegis.investigation_jobs WHERE org_id = $1 AND status = 'running'",
        org,
    )
    assert running == 2


async def test_one_active_investigation_per_incident(app_engine, two_orgs):
    inc = await _incident(app_engine, two_orgs["a"])
    await _investigation(app_engine, two_orgs["a"], inc)
    with pytest.raises(DBAPIError):
        await _investigation(app_engine, two_orgs["a"], inc)


# --- migrations ---------------------------------------------------------------------------------


def test_migration_downgrade_and_upgrade_round_trip(database):
    migrate.downgrade(database["owner"], "base")
    migrate.upgrade(database["owner"], "head")
