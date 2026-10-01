"""Job queue and worker against real PostgreSQL, as aegis_app (ADR-010, prompt §13-14, §30)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from aegis.config import Profile, Settings
from aegis.db.engine import tenant_tx
from aegis.db.tables import (
    audit_events,
    evidence,
    investigation_events,
    investigation_jobs,
    investigations,
)
from aegis.investigations.sources import ScenarioRegistry
from aegis.jobs import queue
from aegis.jobs.queue import IdempotencyConflict, QueueFull, RetryableError, Windows
from aegis.worker.runner import Worker

pytestmark = pytest.mark.db

SCENARIOS = Path(__file__).resolve().parents[3] / "eval" / "scenarios"
NOW = datetime.now(UTC)
WIN = Windows(
    NOW - timedelta(minutes=20), NOW, NOW - timedelta(minutes=80), NOW - timedelta(minutes=20)
)
SCENARIO = {"kind": "scenario", "scenario": "s05-deployment-regression"}


@pytest.fixture(scope="module")
def registry() -> ScenarioRegistry:
    return ScenarioRegistry(SCENARIOS)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        profile=Profile.TEST,
        job_lease_seconds=30,
        job_heartbeat_seconds=1,
        job_max_attempts=3,
        org_running_cap=4,
        org_queue_limit=3,
        worker_concurrency=1,
    )


async def _incident(engine, org) -> uuid.UUID:
    async with tenant_tx(engine, org) as c:
        return (
            await c.execute(
                text(
                    "INSERT INTO incidents (org_id, title, severity, started_at) "
                    "VALUES (:o, 'queue test', 'high', now()) RETURNING id"
                ),
                {"o": org},
            )
        ).scalar_one()


async def _enqueue(
    engine, org, incident, *, key=None, trigger="manual", limit=3, attempts=3, source=None
):
    async with tenant_tx(engine, org) as c:
        return await queue.enqueue_investigation(
            c,
            org_id=org,
            incident_id=incident,
            windows=WIN,
            source=source or SCENARIO,
            trigger=trigger,
            requested_by="test",
            idempotency_key=key,
            queue_limit=limit,
            max_attempts=attempts,
        )


async def _inv(engine, org, inv_id):
    async with tenant_tx(engine, org) as c:
        return (
            (await c.execute(select(investigations).where(investigations.c.id == inv_id)))
            .mappings()
            .one()
        )


async def _job(engine, org, inv_id):
    async with tenant_tx(engine, org) as c:
        return (
            (
                await c.execute(
                    select(investigation_jobs).where(
                        investigation_jobs.c.investigation_id == inv_id
                    )
                )
            )
            .mappings()
            .one()
        )


async def _event_kinds(engine, org, inv_id) -> list[str]:
    async with tenant_tx(engine, org) as c:
        rows = await c.execute(
            select(investigation_events.c.kind)
            .where(investigation_events.c.investigation_id == inv_id)
            .order_by(investigation_events.c.seq)
        )
        return [r[0] for r in rows]


# --- enqueue ------------------------------------------------------------------------------


async def test_enqueue_is_idempotent_and_one_active_per_incident(app_engine, two_orgs):
    org = two_orgs["a"]
    inc = await _incident(app_engine, org)
    first = await _enqueue(app_engine, org, inc, key="k-1")
    assert first.created and first.state == "queued"
    again = await _enqueue(app_engine, org, inc, key="k-1")
    assert (again.investigation_id, again.created, again.state) == (
        first.investigation_id,
        False,
        "existing",
    )
    other_key = await _enqueue(app_engine, org, inc, key="k-2")  # still one active per incident
    assert other_key.investigation_id == first.investigation_id and not other_key.created
    inc2 = await _incident(app_engine, org)
    with pytest.raises(IdempotencyConflict):
        await _enqueue(app_engine, org, inc2, key="k-1")
    assert await _event_kinds(app_engine, org, first.investigation_id) == ["queued"]


async def test_concurrent_starts_create_exactly_one_investigation(
    app_engine, two_orgs, empty_queue
):
    org = two_orgs["a"]
    inc = await _incident(app_engine, org)
    results = await asyncio.gather(*(_enqueue(app_engine, org, inc, limit=50) for _ in range(8)))
    assert sum(r.created for r in results) == 1
    assert len({r.investigation_id for r in results}) == 1
    async with tenant_tx(app_engine, org) as c:
        n = (
            await c.execute(
                select(func.count())
                .select_from(investigations)
                .where(investigations.c.incident_id == inc)
            )
        ).scalar_one()
    assert n == 1


async def test_queue_limit_rejects_manual_and_defers_automatic(
    app_engine, su_conn, empty_queue, settings
):
    org = await su_conn.fetchval(
        "INSERT INTO aegis.organizations (slug, name) VALUES ($1, 'q') RETURNING id",
        f"q-{uuid.uuid4().hex[:8]}",
    )
    for _ in range(3):
        assert (await _enqueue(app_engine, org, await _incident(app_engine, org))).state == "queued"
    with pytest.raises(QueueFull):
        await _enqueue(app_engine, org, await _incident(app_engine, org))
    deferred = await _enqueue(app_engine, org, await _incident(app_engine, org), trigger="auto")
    assert deferred.state == "deferred"
    assert (await _job(app_engine, org, deferred.investigation_id))["status"] == "deferred"
    # nothing promoted while the org is at its limit
    assert await queue.promote_deferred(app_engine, 3) == []
    await su_conn.execute(
        "UPDATE aegis.investigation_jobs SET status = 'cancelled' WHERE org_id = $1 AND status = 'queued'",
        org,
    )
    promoted = await queue.promote_deferred(app_engine, 3)
    assert [p[2] for p in promoted] == [deferred.investigation_id]


# --- worker end to end ----------------------------------------------------------------------


async def test_worker_runs_scenario_end_to_end(
    app_engine, two_orgs, empty_queue, settings, registry
):
    org = two_orgs["a"]
    inc = await _incident(app_engine, org)
    res = await _enqueue(app_engine, org, inc)
    worker = Worker(app_engine, settings, registry, worker_id="w-e2e")
    job = await queue.claim(
        app_engine, worker.worker_id, settings.job_lease_seconds, settings.org_running_cap
    )
    assert job is not None and job.investigation_id == res.investigation_id
    assert await worker.process(job) == "succeeded"

    inv = await _inv(app_engine, org, res.investigation_id)
    assert inv["status"] == "completed"
    assert inv["classification"] in ("CONFIRMED", "PLAUSIBLE")
    assert [rc["service"] for rc in inv["verdict"]["root_causes"]] == ["catalog"]
    assert (await _job(app_engine, org, res.investigation_id))["status"] == "succeeded"
    kinds = await _event_kinds(app_engine, org, res.investigation_id)
    assert kinds[:3] == ["queued", "claimed", "collecting"]
    assert kinds[-4:] == ["analyzing", "hypotheses", "diagnosis", "completed"]
    assert "snapshot_stored" in kinds and "collected" in kinds
    async with tenant_tx(app_engine, org) as c:
        n_evidence = (
            await c.execute(
                select(func.count())
                .select_from(evidence)
                .where(evidence.c.investigation_id == res.investigation_id)
            )
        ).scalar_one()
        actions = [
            r[0]
            for r in await c.execute(
                select(audit_events.c.action).where(
                    audit_events.c.target_id == str(res.investigation_id)
                )
            )
        ]
        assert (await c.execute(text("SELECT audit_verify()"))).scalar_one() is None
    assert n_evidence > 0
    assert "investigation.completed" in actions and "investigation.evidence_collected" in actions
    # tenant B sees none of it
    async with tenant_tx(app_engine, two_orgs["b"]) as c:
        assert (
            await c.execute(
                select(func.count())
                .select_from(evidence)
                .where(evidence.c.investigation_id == res.investigation_id)
            )
        ).scalar_one() == 0
        assert (
            await c.execute(
                select(func.count())
                .select_from(investigation_events)
                .where(investigation_events.c.investigation_id == res.investigation_id)
            )
        ).scalar_one() == 0


async def test_stalled_worker_cannot_write_after_lease_is_reclaimed(
    app_engine, su_conn, two_orgs, empty_queue, settings, registry
):
    org = two_orgs["a"]
    res = await _enqueue(app_engine, org, await _incident(app_engine, org))
    a = Worker(app_engine, settings, registry, worker_id="w-stalled")
    b = Worker(app_engine, settings, registry, worker_id="w-rescuer")
    job_a = await queue.claim(app_engine, a.worker_id, 30, 4)
    # worker A stalls past its lease; B reclaims the job (attempt 2)
    await su_conn.execute(
        "UPDATE aegis.investigation_jobs SET lease_expires_at = now() - interval '1 second' WHERE id = $1",
        job_a.job_id,
    )
    job_b = await queue.claim(app_engine, b.worker_id, 30, 4)
    assert job_b is not None and job_b.job_id == job_a.job_id and job_b.attempt == 2
    # A wakes up: every fenced write is refused, nothing is persisted on its behalf
    async with tenant_tx(app_engine, org) as c:
        assert not await queue.heartbeat(c, job_a, 30)
    assert await a.process(job_a) == "lease_lost"
    async with tenant_tx(app_engine, org) as c:
        assert (
            await c.execute(
                select(func.count())
                .select_from(evidence)
                .where(evidence.c.investigation_id == res.investigation_id)
            )
        ).scalar_one() == 0
    assert (await _job(app_engine, org, res.investigation_id))["lease_owner"] == "w-rescuer"
    # B completes normally
    assert await b.process(job_b) == "succeeded"
    assert (await _inv(app_engine, org, res.investigation_id))["status"] == "completed"


async def test_heartbeat_detects_lost_lease_and_aborts_pipeline(
    app_engine, su_conn, two_orgs, empty_queue, settings, registry, monkeypatch
):
    org = two_orgs["a"]
    res = await _enqueue(app_engine, org, await _incident(app_engine, org))
    w = Worker(app_engine, settings, registry, worker_id="w-slow")
    job = await queue.claim(app_engine, w.worker_id, 30, 4)
    real = registry.snapshot

    def slow_snapshot(scenario_id):
        import time

        time.sleep(3)  # longer than the 1 s heartbeat interval
        return real(scenario_id)

    monkeypatch.setattr(registry, "snapshot", slow_snapshot)
    # another worker takes the job while collection runs
    await su_conn.execute(
        "UPDATE aegis.investigation_jobs SET lease_owner = 'w-thief', attempts = attempts + 1 WHERE id = $1",
        job.job_id,
    )
    assert await w.process(job) == "lease_lost"
    async with tenant_tx(app_engine, org) as c:
        stored = (
            await c.execute(
                text("SELECT count(*) FROM snapshots WHERE investigation_id = :i"),
                {"i": res.investigation_id},
            )
        ).scalar_one()
    assert stored == 0


async def test_crashed_worker_job_is_reaped_when_attempts_are_exhausted(
    app_engine, su_conn, two_orgs, empty_queue, settings, registry
):
    org = two_orgs["a"]
    res = await _enqueue(app_engine, org, await _incident(app_engine, org), attempts=1)
    job = await queue.claim(app_engine, "w-crash", 30, 4)
    assert job is not None
    # the worker process dies; its lease expires
    await su_conn.execute(
        "UPDATE aegis.investigation_jobs SET lease_expires_at = now() - interval '1 second' WHERE id = $1",
        job.job_id,
    )
    assert await queue.claim(app_engine, "w-other", 30, 4) is None  # no attempts left
    await Worker(app_engine, settings, registry, worker_id="w-maint").maintenance_once()
    assert (await _job(app_engine, org, res.investigation_id))["status"] == "dead"
    inv = await _inv(app_engine, org, res.investigation_id)
    assert inv["status"] == "failed" and "lost its lease" in inv["error"]
    assert (await _event_kinds(app_engine, org, res.investigation_id))[-1] == "failed"


async def test_crashed_worker_job_is_reclaimed_while_attempts_remain(
    app_engine, su_conn, two_orgs, empty_queue, settings, registry
):
    org = two_orgs["a"]
    res = await _enqueue(app_engine, org, await _incident(app_engine, org), attempts=3)
    job = await queue.claim(app_engine, "w-crash", 30, 4)
    await su_conn.execute(
        "UPDATE aegis.investigation_jobs SET lease_expires_at = now() - interval '1 second' WHERE id = $1",
        job.job_id,
    )
    w = Worker(app_engine, settings, registry, worker_id="w-next")
    again = await queue.claim(app_engine, w.worker_id, 30, 4)
    assert again is not None and again.attempt == 2
    assert await w.process(again) == "succeeded"
    assert (await _inv(app_engine, org, res.investigation_id))["status"] == "completed"


async def test_retryable_failure_is_rescheduled_then_fails_permanently(
    app_engine, two_orgs, empty_queue, settings, registry, monkeypatch
):
    org = two_orgs["a"]
    res = await _enqueue(app_engine, org, await _incident(app_engine, org), attempts=2)

    def flaky(_scenario_id):
        raise RetryableError("telemetry backend timed out")

    monkeypatch.setattr(registry, "snapshot", flaky)
    w = Worker(app_engine, settings, registry, worker_id="w-retry")
    job = await queue.claim(app_engine, w.worker_id, 30, 4)
    assert await w.process(job) == "retry_scheduled"
    j = await _job(app_engine, org, res.investigation_id)
    assert (
        j["status"] == "queued"
        and j["available_at"] > datetime.now(UTC)
        and j["lease_owner"] is None
    )
    assert (await _inv(app_engine, org, res.investigation_id))["status"] == "queued"
    assert await queue.claim(app_engine, w.worker_id, 30, 4) is None  # backoff not elapsed
    async with tenant_tx(app_engine, org) as c:
        await c.execute(
            text("UPDATE investigation_jobs SET available_at = now() WHERE investigation_id = :i"),
            {"i": res.investigation_id},
        )
    job2 = await queue.claim(app_engine, w.worker_id, 30, 4)
    assert job2.attempt == 2
    assert await w.process(job2) == "failed"
    inv = await _inv(app_engine, org, res.investigation_id)
    assert inv["status"] == "failed" and inv["error"] == "telemetry backend timed out"
    assert (await _job(app_engine, org, res.investigation_id))["status"] == "failed"


async def test_unexpected_errors_fail_permanently_without_leaking_details(
    app_engine, two_orgs, empty_queue, settings, registry, monkeypatch
):
    org = two_orgs["a"]
    res = await _enqueue(app_engine, org, await _incident(app_engine, org))

    def broken(_scenario_id):
        raise ValueError("password=hunter2 in http://user:secret@prom:9090")

    monkeypatch.setattr(registry, "snapshot", broken)
    w = Worker(app_engine, settings, registry, worker_id="w-bug")
    assert await w.process(await queue.claim(app_engine, w.worker_id, 30, 4)) == "failed"
    inv = await _inv(app_engine, org, res.investigation_id)
    assert inv["status"] == "failed"
    assert "hunter2" not in inv["error"] and "secret" not in inv["error"]
    assert "ValueError" in inv["error"]


async def test_cancel_queued_investigation_is_immediate(app_engine, two_orgs, empty_queue):
    org = two_orgs["a"]
    res = await _enqueue(app_engine, org, await _incident(app_engine, org))
    async with tenant_tx(app_engine, org) as c:
        assert await queue.request_cancel(c, org, res.investigation_id, "tester") == "cancelled"
    assert (await _inv(app_engine, org, res.investigation_id))["status"] == "cancelled"
    assert (await _job(app_engine, org, res.investigation_id))["status"] == "cancelled"
    assert await queue.claim(app_engine, "w", 30, 4) is None
    async with tenant_tx(app_engine, org) as c:
        assert await queue.request_cancel(c, org, res.investigation_id, "tester") == "not_active"


async def test_cancel_running_investigation_stops_at_next_stage(
    app_engine, two_orgs, empty_queue, settings, registry
):
    org = two_orgs["a"]
    res = await _enqueue(app_engine, org, await _incident(app_engine, org))
    w = Worker(app_engine, settings, registry, worker_id="w-cancel")
    job = await queue.claim(app_engine, w.worker_id, 30, 4)
    async with tenant_tx(app_engine, org) as c:
        assert (
            await queue.request_cancel(c, org, res.investigation_id, "tester") == "cancel_requested"
        )
    assert await w.process(job) == "cancelled"
    assert (await _inv(app_engine, org, res.investigation_id))["status"] == "cancelled"
    assert (await _job(app_engine, org, res.investigation_id))["status"] == "cancelled"
    kinds = await _event_kinds(app_engine, org, res.investigation_id)
    assert kinds[-2:] == ["cancel_requested", "cancelled"]


async def test_cancel_from_other_tenant_is_not_found(app_engine, two_orgs, empty_queue):
    res = await _enqueue(app_engine, two_orgs["a"], await _incident(app_engine, two_orgs["a"]))
    async with tenant_tx(app_engine, two_orgs["b"]) as c:
        assert (
            await queue.request_cancel(c, two_orgs["b"], res.investigation_id, "intruder")
            == "not_found"
        )
    assert (await _inv(app_engine, two_orgs["a"], res.investigation_id))["status"] == "queued"
