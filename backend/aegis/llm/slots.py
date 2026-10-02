"""Global LLM slot pool (ADR-010): bounds concurrent model calls across every worker.

A slot is a lease row taken through ``acquire_llm_slot`` (SECURITY DEFINER). The holder renews
it while the call runs; a crashed worker's slot frees itself when the lease expires. With one
CPU-bound local model the pool size is 1, so investigations queue for the model instead of
thrashing it.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aegis.db.engine import system_tx


async def acquire(engine: AsyncEngine, holder: str, slots: int, lease_seconds: int) -> int | None:
    async with system_tx(engine) as c:
        slot: int | None = (
            await c.execute(
                text("SELECT aegis.acquire_llm_slot(:h, :l, :n)"),
                {"h": holder, "l": lease_seconds, "n": slots},
            )
        ).scalar_one_or_none()
    return slot


async def release(engine: AsyncEngine, slot: int, holder: str) -> None:
    async with system_tx(engine) as c:
        await c.execute(text("SELECT aegis.release_llm_slot(:s, :h)"), {"s": slot, "h": holder})


async def renew(engine: AsyncEngine, slot: int, holder: str, lease_seconds: int) -> bool:
    async with system_tx(engine) as c:
        ok: bool = (
            await c.execute(
                text("SELECT aegis.renew_llm_slot(:s, :h, :l)"),
                {"s": slot, "h": holder, "l": lease_seconds},
            )
        ).scalar_one()
    return ok


@contextlib.asynccontextmanager
async def hold(
    engine: AsyncEngine,
    holder: str,
    slots: int,
    lease_seconds: int,
    wait_seconds: float,
    poll: float = 1.0,
) -> AsyncIterator[int | None]:
    """Yield a slot number (or None if none became free within ``wait_seconds``); renew it in
    the background while held; always release it."""
    deadline = time.monotonic() + wait_seconds
    slot = await acquire(engine, holder, slots, lease_seconds)
    while slot is None and time.monotonic() < deadline:
        await asyncio.sleep(poll)
        slot = await acquire(engine, holder, slots, lease_seconds)
    if slot is None:
        yield None
        return

    async def keep() -> None:
        while True:
            await asyncio.sleep(max(1.0, lease_seconds / 3))
            await renew(engine, slot, holder, lease_seconds)

    renewer = asyncio.create_task(keep())
    try:
        yield slot
    finally:
        renewer.cancel()
        await asyncio.gather(renewer, return_exceptions=True)
        await release(engine, slot, holder)
