"""Database engine and tenant-scoped transactions (ADR-012).

Every query against tenant data runs inside ``tenant_tx``: one transaction whose first
statement sets ``aegis.org_id`` (and optionally ``aegis.user_id``) with transaction scope
(``set_config(..., true)``), so a pooled connection never carries a tenant into the next
transaction. Pre-tenant operations (session/API-key lookup, job claim) use the SECURITY
DEFINER functions through ``system_tx``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from aegis.config import Settings


def make_engine(settings: Settings, url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        url or settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "server_settings": {
                "application_name": "aegis",
                "statement_timeout": str(settings.db_statement_timeout_ms),
            }
        },
    )


@asynccontextmanager
async def tenant_tx(
    engine: AsyncEngine, org_id: UUID, user_id: UUID | None = None
) -> AsyncIterator[AsyncConnection]:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "SELECT set_config('aegis.org_id', :org, true), set_config('aegis.user_id', :usr, true)"
            ),
            {"org": str(org_id), "usr": str(user_id) if user_id else ""},
        )
        yield conn


@asynccontextmanager
async def system_tx(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A transaction with no tenant set: tenant tables are invisible (fail closed); only the
    SECURITY DEFINER functions return data."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT set_config('aegis.org_id', '', true)"))
        yield conn
