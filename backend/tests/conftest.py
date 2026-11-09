"""PostgreSQL fixtures. Each test session gets a fresh database bootstrapped with the real
roles and migrated as the owner. Tests connect as ``aegis_app`` — never as the superuser —
so row-level security is actually exercised (Council C).

Requires AEGIS_TEST_SUPERUSER_URL (default: the local dev container on port 55432). Without it the
database tests are skipped, unless AEGIS_REQUIRE_DB=1 (set in CI), which makes them fail instead.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from urllib.parse import quote

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from aegis.db import bootstrap, migrate

SUPERUSER_URL = os.environ.get(
    "AEGIS_TEST_SUPERUSER_URL", "postgresql://postgres:devsuperuser@127.0.0.1:55432/postgres"
)
OWNER_PW = "test-owner-password"
# Hostile on purpose: dollar quotes, quotes and a statement terminator. Every aegis_app
# connection in the suite therefore proves the bootstrap cannot be broken out of.
APP_PW = "app-pw-$$'; DROP ROLE aegis_owner; --$q$"


def _server() -> str:
    return SUPERUSER_URL.rsplit("/", 1)[0].split("://", 1)[1].split("@", 1)[1]


async def _available() -> bool:
    try:
        conn = await asyncpg.connect(SUPERUSER_URL, timeout=3)
        await conn.close()
        return True
    except (OSError, asyncpg.PostgresError, TimeoutError):
        return False


def skip_or_fail_without_db(reason: str) -> None:
    """Database tests are skipped on a machine without PostgreSQL, but fail when
    ``AEGIS_REQUIRE_DB=1`` (CI): a green run must never hide the skipped database tests."""
    if os.environ.get("AEGIS_REQUIRE_DB") == "1":
        pytest.fail(f"{reason} (AEGIS_REQUIRE_DB=1)", pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def database() -> Iterator[dict[str, str]]:
    if not asyncio.run(_available()):
        skip_or_fail_without_db(f"PostgreSQL not reachable at {SUPERUSER_URL}")
    name = f"aegis_test_{secrets.token_hex(4)}"

    # roles are cluster-wide: bootstrap (re)asserts their attributes and passwords
    asyncio.run(bootstrap.bootstrap(SUPERUSER_URL, OWNER_PW, APP_PW, name))
    app_pw = quote(APP_PW, safe="")
    urls = {
        "name": name,
        "owner": f"postgresql+asyncpg://aegis_owner:{OWNER_PW}@{_server()}/{name}",
        "app": f"postgresql+asyncpg://aegis_app:{app_pw}@{_server()}/{name}",
        "superuser": SUPERUSER_URL.rsplit("/", 1)[0] + f"/{name}",
    }
    migrate.upgrade(urls["owner"])
    yield urls

    async def teardown() -> None:
        conn = await asyncpg.connect(SUPERUSER_URL)
        try:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", name
            )
            await conn.execute(f"DROP DATABASE IF EXISTS {name}")
        finally:
            await conn.close()

    asyncio.run(teardown())


@pytest_asyncio.fixture
async def app_engine(database: dict[str, str]) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database["app"], pool_size=5)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def owner_conn(database: dict[str, str]) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(database["owner"].replace("+asyncpg", ""))
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def su_conn(database: dict[str, str]) -> AsyncIterator[asyncpg.Connection]:
    """Superuser connection used ONLY to create fixtures (orgs, users), never for assertions."""
    conn = await asyncpg.connect(database["superuser"])
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def two_orgs(su_conn: asyncpg.Connection) -> dict[str, uuid.UUID]:
    ids = {}
    for name in ("a", "b"):
        slug = f"org-{name}-{secrets.token_hex(3)}"
        ids[name] = await su_conn.fetchval(
            "INSERT INTO aegis.organizations (slug, name) VALUES ($1, $1) RETURNING id", slug
        )
    return ids


@pytest_asyncio.fixture
async def empty_queue(su_conn: asyncpg.Connection) -> None:
    """claim_job is global across organizations: claim tests start from an empty queue."""
    await su_conn.execute("DELETE FROM aegis.investigation_jobs")


@dataclass
class Tenant:
    org_id: uuid.UUID
    slug: str


@pytest_asyncio.fixture
async def tenant_factory(su_conn):
    async def make(name: str = "t") -> Tenant:
        slug = f"{name}-{secrets.token_hex(4)}"
        org_id = await su_conn.fetchval(
            "INSERT INTO aegis.organizations (slug, name) VALUES ($1, $1) RETURNING id", slug
        )
        return Tenant(org_id, slug)

    return make
