"""``aegis admin create-api-key``: the minted key authenticates on its scope only, the secret goes
to the output file and never to stdout/stderr, and the file is written atomically (0644 on
POSIX, so the read-only demo consumers can read it)."""

from __future__ import annotations

import os
import stat

import pytest
from pydantic import SecretStr

from aegis import cli
from tests.api.conftest import alertmanager_payload, bearer

pytestmark = pytest.mark.db

CHANGE = {"source": "x", "kind": "config", "summary": "s", "ts": "2026-10-02T10:00:00Z"}


@pytest.fixture
def cli_settings(settings, database):
    return settings.model_copy(update={"migration_database_url": SecretStr(database["owner"])})


async def test_minted_key_works_on_its_scope_and_is_never_printed(
    tenant_factory, cli_settings, client_factory, su_conn, tmp_path, capsys
):
    t = await tenant_factory("ck")
    out = tmp_path / "keys" / "alert-ingest-key"
    prefix = await cli.create_api_key(
        cli_settings, t.slug, "alertmanager", ["ingest:alerts"], 30, out
    )
    secret = out.read_text(encoding="utf-8").strip()
    assert prefix and prefix in secret
    assert sorted(p.name for p in out.parent.iterdir()) == ["alert-ingest-key"]  # no temp file
    if os.name == "posix":
        assert stat.S_IMODE(out.stat().st_mode) == 0o644

    client = await client_factory()
    r = await client.post(
        "/api/v1/alerts/alertmanager", json=alertmanager_payload(), headers=bearer(secret)
    )
    assert r.status_code == 202, r.text
    r = await client.post("/api/v1/change-events", json=CHANGE, headers=bearer(secret))
    assert r.status_code == 403

    captured = capsys.readouterr()
    assert secret not in captured.out and secret not in captured.err
    row = await su_conn.fetchrow(
        "SELECT actor_type, actor_id, details FROM aegis.audit_events "
        "WHERE org_id = $1 AND action = 'api_key.created'",
        t.org_id,
    )
    assert (row["actor_type"], row["actor_id"]) == ("system", "operator-cli")
    assert secret not in row["details"]


def test_cli_entry_point_prints_only_the_prefix(
    tenant_factory, database, monkeypatch, capsys, tmp_path
):
    import asyncio

    slug = f"ckcli-{os.urandom(3).hex()}"
    asyncio.run(cli.create_org(database["owner"].replace("+asyncpg", ""), slug, "CK", None))
    out = tmp_path / "change-ingest-key"
    monkeypatch.setenv("AEGIS_PROFILE", "test")
    monkeypatch.setenv("AEGIS_MIGRATION_DATABASE_URL", database["owner"])
    cli.get_settings.cache_clear()
    try:
        argv = [
            "admin",
            "create-api-key",
            "--org",
            slug,
            "--scope",
            "ingest:changes",
            "--out",
            str(out),
        ]
        assert cli.main(argv) == 0
        secret = out.read_text(encoding="utf-8").strip()
        printed = capsys.readouterr()
        assert "ingest:changes" in printed.out and str(out) in printed.out
        assert secret not in printed.out and secret not in printed.err
        with pytest.raises(SystemExit):
            cli.main(
                [
                    "admin",
                    "create-api-key",
                    "--org",
                    "no-such-org",
                    "--scope",
                    "read",
                    "--out",
                    str(out),
                ]
            )
        assert out.read_text(encoding="utf-8").strip() == secret  # unchanged on failure
        with pytest.raises(SystemExit):
            cli.main(
                ["admin", "create-api-key", "--org", slug, "--scope", "admin", "--out", str(out)]
            )
    finally:
        cli.get_settings.cache_clear()
