"""Tenant context cannot leak through pooled connections (ADR-012, prompt §8).

The engine is forced to a single pooled connection so every transaction below provably runs
on the same PostgreSQL backend (asserted via ``pg_backend_pid()``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from aegis.db.engine import system_tx, tenant_tx

pytestmark = pytest.mark.db

PACKAGE = Path(__file__).resolve().parents[2] / "aegis"


@pytest.fixture
async def one_conn_engine(database):
    engine = create_async_engine(database["app"], pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()


async def _seed(engine, org) -> None:
    async with tenant_tx(engine, org) as c:
        await c.execute(
            text(
                "INSERT INTO incidents (org_id, title, severity, started_at) "
                "VALUES (:o, 'pool test', 'low', now())"
            ),
            {"o": org},
        )


async def _bare_state(engine) -> tuple[int, str | None, int]:
    async with engine.begin() as c:
        row = (
            await c.execute(
                text(
                    "SELECT pg_backend_pid(), current_setting('aegis.org_id', true), "
                    "(SELECT count(*) FROM incidents)"
                )
            )
        ).one()
    return row[0], row[1], row[2]


async def _pid_in_tenant(engine, org) -> int:
    async with tenant_tx(engine, org) as c:
        return (await c.execute(text("SELECT pg_backend_pid()"))).scalar_one()


async def test_tenant_setting_does_not_survive_commit(one_conn_engine, two_orgs):
    await _seed(one_conn_engine, two_orgs["a"])
    pid = await _pid_in_tenant(one_conn_engine, two_orgs["a"])
    bare_pid, setting, visible = await _bare_state(one_conn_engine)
    assert bare_pid == pid  # same pooled backend
    assert setting in ("", None)
    assert visible == 0


async def test_tenant_setting_does_not_survive_python_exception(one_conn_engine, two_orgs):
    await _seed(one_conn_engine, two_orgs["a"])
    with pytest.raises(RuntimeError):
        async with tenant_tx(one_conn_engine, two_orgs["a"]) as c:
            assert (await c.execute(text("SELECT count(*) FROM incidents"))).scalar_one() > 0
            raise RuntimeError("handler crashed mid-request")
    _, setting, visible = await _bare_state(one_conn_engine)
    assert setting in ("", None) and visible == 0


async def test_tenant_setting_does_not_survive_database_error(one_conn_engine, two_orgs):
    await _seed(one_conn_engine, two_orgs["a"])
    with pytest.raises(DBAPIError):
        async with tenant_tx(one_conn_engine, two_orgs["a"]) as c:
            await c.execute(text("SELECT 1 / 0"))
    _, setting, visible = await _bare_state(one_conn_engine)
    assert setting in ("", None) and visible == 0


async def test_next_tenant_on_same_connection_sees_only_its_rows(one_conn_engine, two_orgs):
    await _seed(one_conn_engine, two_orgs["a"])
    async with tenant_tx(one_conn_engine, two_orgs["a"]) as c:
        a_ids = {r[0] for r in (await c.execute(text("SELECT org_id FROM incidents"))).all()}
    async with tenant_tx(one_conn_engine, two_orgs["b"]) as c:
        b_ids = {r[0] for r in (await c.execute(text("SELECT org_id FROM incidents"))).all()}
    assert a_ids == {two_orgs["a"]}
    assert b_ids <= {two_orgs["b"]}


async def test_system_tx_after_tenant_tx_sees_nothing(one_conn_engine, two_orgs):
    await _seed(one_conn_engine, two_orgs["a"])
    async with system_tx(one_conn_engine) as c:
        assert (await c.execute(text("SELECT count(*) FROM incidents"))).scalar_one() == 0


async def test_app_role_cannot_create_objects_to_hijack_search_path(app_engine):
    """SECURITY DEFINER functions pin search_path; the app role additionally cannot create
    objects anywhere a function could resolve them (no TEMP, no CREATE on any schema)."""
    for stmt in (
        "CREATE TEMP TABLE hijack (x int)",
        "CREATE TABLE public.hijack (x int)",
        "CREATE TABLE aegis.hijack (x int)",
        "CREATE FUNCTION aegis.current_org() RETURNS uuid LANGUAGE sql AS 'SELECT NULL::uuid'",
    ):
        with pytest.raises((ProgrammingError, DBAPIError)):
            async with app_engine.begin() as c:
                await c.execute(text(stmt))


def test_no_session_scoped_tenant_settings_in_source():
    """Only transaction-scoped settings are allowed: set_config(..., true) or SET LOCAL."""
    session_set_config = re.compile(r"set_config\(\s*'aegis\.\w+'\s*,[^)]*,\s*false\s*\)", re.I)
    session_set = re.compile(r"\bSET\s+(SESSION\s+)?aegis\.", re.I)
    offenders = []
    for path in PACKAGE.rglob("*"):
        if path.suffix not in (".py", ".sql"):
            continue
        source = path.read_text(encoding="utf-8")
        if session_set_config.search(source) or session_set.search(source):
            offenders.append(str(path.relative_to(PACKAGE)))
    assert not offenders, offenders
