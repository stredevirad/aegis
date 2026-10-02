"""Operator CLI against real PostgreSQL: organization bootstrap, invitations, disabling users,
audit verification; and migration as the owner through the CLI entry point."""

from __future__ import annotations

import secrets

import pytest

from aegis import cli

pytestmark = pytest.mark.db


@pytest.fixture
def owner_dsn(database) -> str:
    return database["owner"].replace("+asyncpg", "")


async def test_create_org_is_idempotent_and_invites_admin(owner_dsn, su_conn):
    slug = f"cli-{secrets.token_hex(3)}"
    org_id = await cli.create_org(owner_dsn, slug, "CLI Org", "Admin@Example.com")
    assert await cli.create_org(owner_dsn, slug, "CLI Org", "admin@example.com") == org_id
    invites = await su_conn.fetch(
        "SELECT email, role FROM aegis.invitations WHERE org_id = $1", org_id
    )
    assert [(r["email"], r["role"]) for r in invites] == [("admin@example.com", "admin")]
    await cli.invite(owner_dsn, slug, "viewer@example.com", "viewer")
    with pytest.raises(SystemExit):
        await cli.invite(owner_dsn, "no-such-org", "x@example.com", "viewer")
    assert await cli.verify_audit(owner_dsn, slug) is None


async def test_disable_user_ends_sessions_and_revokes_keys(owner_dsn, su_conn, two_orgs):
    email = f"dis-{secrets.token_hex(3)}@example.com"
    user = await su_conn.fetchval(
        "INSERT INTO aegis.users (issuer, subject, email, email_verified) VALUES ('i', $1, $1, true) RETURNING id",
        email,
    )
    for org in two_orgs.values():
        await su_conn.execute(
            "INSERT INTO aegis.memberships (org_id, user_id, role) VALUES ($1, $2, 'admin')",
            org,
            user,
        )
        await su_conn.execute(
            "INSERT INTO aegis.sessions (token_hash, org_id, user_id, idle_expires_at, expires_at) "
            "VALUES ($1, $2, $3, now() + interval '1 hour', now() + interval '1 day')",
            secrets.token_bytes(32),
            org,
            user,
        )
        await su_conn.execute(
            "INSERT INTO aegis.api_keys (org_id, name, prefix, secret_hash, scopes, created_by, expires_at) "
            "VALUES ($1, 'k', $2, $3, ARRAY['read'], $4, now() + interval '1 day')",
            org,
            "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz234567") for _ in range(8)),
            secrets.token_bytes(32),
            user,
        )
    assert await cli.set_user_disabled(owner_dsn, email.upper(), True) == 1
    assert await su_conn.fetchval("SELECT disabled FROM aegis.users WHERE id = $1", user) is True
    assert (
        await su_conn.fetchval("SELECT count(*) FROM aegis.sessions WHERE user_id = $1", user) == 0
    )
    assert (
        await su_conn.fetchval(
            "SELECT count(*) FROM aegis.api_keys WHERE created_by = $1 AND revoked_at IS NULL", user
        )
        == 0
    )
    await cli.set_user_disabled(owner_dsn, email, False)
    assert await su_conn.fetchval("SELECT disabled FROM aegis.users WHERE id = $1", user) is False


async def test_operator_functions_are_not_executable_by_the_app_role(app_engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    for stmt in (
        "SELECT aegis.org_id_for_slug('x')",
        "SELECT * FROM aegis.admin_set_user_disabled('x', true)",
    ):
        with pytest.raises(DBAPIError):
            async with app_engine.begin() as c:
                await c.execute(text(stmt))


def test_cli_rejects_unsafe_configuration_for_processes(monkeypatch, capsys):
    monkeypatch.setenv("AEGIS_PROFILE", "production")
    cli.get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exc:
            cli.main(["worker"])
        assert exc.value.code == 2
        assert "configuration error" in capsys.readouterr().err
    finally:
        cli.get_settings.cache_clear()
