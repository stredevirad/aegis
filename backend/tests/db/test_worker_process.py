"""The worker as a running process loop: concurrency, graceful stop, mid-job kill + reclaim."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from aegis.config import Profile, Settings
from aegis.db.engine import tenant_tx
from aegis.db.tables import investigation_jobs, investigations
from aegis.investigations.sources import ScenarioRegistry
from aegis.jobs import queue
from aegis.jobs.queue import Windows
from aegis.worker.runner import Worker

pytestmark = pytest.mark.db

SCENARIOS = Path(__file__).resolve().parents[3] / "eval" / "scenarios"
NOW = datetime.now(UTC)
WIN = Windows(
    NOW - timedelta(minutes=20), NOW, NOW - timedelta(minutes=80), NOW - timedelta(minutes=20)
)


@pytest.fixture(scope="module")
def registry() -> ScenarioRegistry:
    return ScenarioRegistry(SCENARIOS)


def _settings(**kw) -> Settings:
    base = {
        "profile": Profile.TEST,
        "job_lease_seconds": 30,
        "job_heartbeat_seconds": 1,
        "worker_concurrency": 2,
    }
    return Settings(**{**base, **kw})


async def _enqueue_n(engine, org, n, scenario="s02-http-5xx-internal") -> list[uuid.UUID]:
    ids = []
    for _ in range(n):
        async with tenant_tx(engine, org) as c:
            inc = (
                await c.execute(
                    text(
                        "INSERT INTO incidents (org_id, title, severity, started_at) "
                        "VALUES (:o, 'p', 'high', now()) RETURNING id"
                    ),
                    {"o": org},
                )
            ).scalar_one()
            res = await queue.enqueue_investigation(
                c,
                org_id=org,
                incident_id=inc,
                windows=WIN,
                source={"kind": "scenario", "scenario": scenario},
                trigger="manual",
                requested_by="t",
                idempotency_key=None,
                queue_limit=100,
                max_attempts=3,
            )
            ids.append(res.investigation_id)
    return ids


async def _statuses(engine, org, ids) -> dict[uuid.UUID, str]:
    async with tenant_tx(engine, org) as c:
        rows = await c.execute(
            select(investigations.c.id, investigations.c.status).where(investigations.c.id.in_(ids))
        )
        return {r.id: r.status for r in rows}


async def _wait_until(predicate, limit=60.0, every=0.2):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(every)
    return False


async def test_run_loop_processes_jobs_concurrently_and_stops_cleanly(
    app_engine, two_orgs, empty_queue, registry, tmp_path
):
    org = two_orgs["a"]
    ids = await _enqueue_n(app_engine, org, 4)
    health = tmp_path / "worker.alive"
    w = Worker(
        app_engine,
        _settings(worker_health_file=health),
        registry,
        worker_id="w-run",
        poll_seconds=0.1,
    )
    stop = asyncio.Event()
    runner = asyncio.create_task(w.run(stop))

    async def all_done():
        return set((await _statuses(app_engine, org, ids)).values()) == {"completed"}

    assert await _wait_until(all_done), await _statuses(app_engine, org, ids)
    stop.set()
    await asyncio.wait_for(runner, timeout=15)
    assert w.processed["succeeded"] == 4
    assert health.exists()


async def test_killed_worker_job_is_reclaimed_by_another_worker(
    app_engine, su_conn, two_orgs, empty_queue, registry, monkeypatch
):
    org = two_orgs["a"]
    [inv_id] = await _enqueue_n(app_engine, org, 1)
    real = registry.snapshot

    def slow(scenario_id):
        time.sleep(1.5)
        return real(scenario_id)

    monkeypatch.setattr(registry, "snapshot", slow)
    a = Worker(
        app_engine,
        _settings(worker_shutdown_grace_seconds=0),
        registry,
        worker_id="w-killed",
        poll_seconds=0.1,
    )
    stop = asyncio.Event()
    runner = asyncio.create_task(a.run(stop))

    async def claimed():
        async with tenant_tx(app_engine, org) as c:
            row = (
                await c.execute(
                    select(investigation_jobs.c.status, investigation_jobs.c.lease_owner).where(
                        investigation_jobs.c.investigation_id == inv_id
                    )
                )
            ).one()
        return row.status == "running" and row.lease_owner == "w-killed"

    assert await _wait_until(claimed, limit=20)
    stop.set()  # grace 0: the in-flight job is abandoned mid-pipeline
    await asyncio.wait_for(runner, timeout=15)
    assert (await _statuses(app_engine, org, [inv_id]))[inv_id] == "running"
    # the process is gone; its lease expires and another worker picks the job up
    await su_conn.execute(
        "UPDATE aegis.investigation_jobs SET lease_expires_at = now() - interval '1 second' WHERE investigation_id = $1",
        inv_id,
    )
    monkeypatch.setattr(registry, "snapshot", real)
    b = Worker(app_engine, _settings(), registry, worker_id="w-rescue")
    job = await queue.claim(app_engine, b.worker_id, 30, 4)
    assert job is not None and job.investigation_id == inv_id and job.attempt == 2
    assert await b.process(job) == "succeeded"
    assert (await _statuses(app_engine, org, [inv_id]))[inv_id] == "completed"
