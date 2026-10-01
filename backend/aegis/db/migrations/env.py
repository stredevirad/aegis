"""Alembic environment. Migrations run as the owning role (AEGIS_MIGRATION_DATABASE_URL)."""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("AEGIS_MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("AEGIS_MIGRATION_DATABASE_URL is not set (owner role connection)")
    return url


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table="alembic_version",
        version_table_schema="aegis",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = async_engine_from_config(
        {"sqlalchemy.url": _url()}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), literal_binds=True, version_table_schema="aegis")
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
