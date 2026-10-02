"""Steady synthetic load against the demo frontend (RATE requests per second)."""

from __future__ import annotations

import asyncio
import os
import random

import httpx

TARGET = os.environ.get("TARGET", "http://frontend:8080/api/work")
RATE = float(os.environ.get("RATE", "8"))


async def main() -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            asyncio.create_task(hit(client))  # noqa: RUF006 - fire and forget
            await asyncio.sleep(random.expovariate(RATE))  # noqa: S311


async def hit(client: httpx.AsyncClient) -> None:
    try:
        await client.get(TARGET)
    except httpx.HTTPError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
