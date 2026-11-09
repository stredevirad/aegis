"""Fault isolation of the worker's maintenance loop (no database needed).

The loop reaps dead jobs, promotes deferred ones, runs due verifications and reconciles stuck
remediations. It runs as a background task that nobody awaits until shutdown, so an exception
that escapes it ends all of that silently while the job slots keep working."""

from __future__ import annotations

import asyncio
import logging

import pytest

from aegis.config import Profile, Settings
from aegis.worker.runner import Worker

REAL_WAIT_FOR = asyncio.wait_for


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> Worker:
    async def fast_wait_for(aw, *args, **kwargs):
        # the loop's 10 s pause between rounds; any other timeout is left alone
        if kwargs.get("timeout") == 10:
            kwargs["timeout"] = 0.01
        return await REAL_WAIT_FOR(aw, *args, **kwargs)

    monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)
    return Worker(engine=None, settings=Settings(profile=Profile.TEST), scenarios=None)


async def test_maintenance_loop_survives_an_unexpected_exception(
    worker: Worker, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stop = asyncio.Event()
    rounds = 0

    async def flaky() -> None:
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            raise RuntimeError("unexpected bug in one maintenance step")
        stop.set()

    monkeypatch.setattr(worker, "maintenance_once", flaky)
    with caplog.at_level(logging.ERROR, logger="aegis.worker"):
        await REAL_WAIT_FOR(worker._maintenance(stop), 5)
    assert rounds == 2, "the loop must run another round after an unexpected exception"
    failures = [r for r in caplog.records if r.name == "aegis.worker" and r.exc_info]
    assert failures and failures[0].exc_info is not None
    assert failures[0].exc_info[0] is RuntimeError


async def test_maintenance_loop_still_stops_on_cancellation(
    worker: Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CancelledError is a BaseException: catching Exception must not swallow it, or the
    worker could not shut the loop down."""
    started = asyncio.Event()

    async def hangs() -> None:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "maintenance_once", hangs)
    task = asyncio.create_task(worker._maintenance(asyncio.Event()))
    await REAL_WAIT_FOR(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await REAL_WAIT_FOR(task, 5)
    assert task.cancelled()
