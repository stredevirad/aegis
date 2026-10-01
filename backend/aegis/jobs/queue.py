"""Investigation job queue on PostgreSQL (ADR-010).

State model (``investigation_jobs.status`` + ``investigations.cancel_requested``), mapped to
the lifecycle names used in the documentation:

=====================  =================================================================
lifecycle              representation
=====================  =================================================================
pending                ``queued`` with ``available_at <= now()``
deferred               ``deferred``: organization over its queue limit; promoted later,
                       never dropped (``promote_deferred_jobs``)
claimed / running      ``running`` with ``lease_owner``/``lease_expires_at`` (the stage is
                       visible in ``investigation_events``)
cancelling             ``running`` and ``investigations.cancel_requested``
retryable failure      ``queued`` with ``attempts > 0``, ``available_at`` in the future and
                       ``last_error`` set (jittered exponential backoff)
permanent failure      ``failed`` (non-retryable error or attempts exhausted) or ``dead``
                       (lease expired with no attempts left; set by the reaper)
succeeded / cancelled  ``succeeded`` / ``cancelled``
=====================  =================================================================

Fencing: every write a worker makes on behalf of a claimed job carries
``(id, status='running', lease_owner, attempts)`` in its WHERE clause. A worker that stalled
past its lease and lost the job to another worker can therefore neither heartbeat, nor
complete, nor fail the job, and its result transaction (which starts with the fenced update)
rolls back.

Lock order: a transaction that touches both an investigation and its job locks the
investigation row first (``lock_investigation``), then the job row. Heartbeats touch only
the job row. This order is used by the API (cancel) and the worker alike, so they cannot
deadlock.

Investigation jobs only read external systems and write their own rows transactionally, so
retrying them is safe. Remediation is not executed through this queue's retry path.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from aegis.db.engine import system_tx
from aegis.db.tables import investigation_jobs as jobs
from aegis.db.tables import investigations
from aegis.jobs import events
from aegis.jobs.events import EventKind

ACTIVE_INVESTIGATION = ("queued", "running")
TERMINAL_INVESTIGATION = ("completed", "failed", "cancelled")
# Automatic investigations beyond (queue_limit * factor) deferred jobs are not recorded as
# jobs at all (the incident still exists and can be investigated manually).
DEFERRED_FACTOR = 10


class QueueFull(Exception):
    """The organization's queue is at its limit (manual starts get HTTP 429)."""


class IdempotencyConflict(Exception):
    """The idempotency key was already used for a different incident."""


class LeaseLost(Exception):
    """The job was reclaimed by another worker; this worker must stop writing."""


class RetryableError(Exception):
    """A transient failure (provider timeout, worker-side resource issue): retry with backoff."""


@dataclass(frozen=True)
class Windows:
    incident_start: datetime
    incident_end: datetime
    baseline_start: datetime
    baseline_end: datetime


@dataclass(frozen=True)
class EnqueueResult:
    investigation_id: UUID | None
    created: bool
    state: Literal["queued", "deferred", "existing", "skipped"]


@dataclass(frozen=True)
class ClaimedJob:
    job_id: UUID
    org_id: UUID
    investigation_id: UUID
    attempt: int
    worker: str


# --- enqueue (API / ingestion, inside the caller's tenant transaction) ---------------------


async def _existing(
    conn: AsyncConnection, org_id: UUID, incident_id: UUID, idempotency_key: str | None
) -> EnqueueResult | None:
    if idempotency_key:
        row = (
            await conn.execute(
                select(investigations.c.id, investigations.c.incident_id).where(
                    investigations.c.org_id == org_id,
                    investigations.c.idempotency_key == idempotency_key,
                )
            )
        ).first()
        if row is not None:
            if row.incident_id != incident_id:
                raise IdempotencyConflict(idempotency_key)
            return EnqueueResult(row.id, created=False, state="existing")
    active = (
        await conn.execute(
            select(investigations.c.id).where(
                investigations.c.org_id == org_id,
                investigations.c.incident_id == incident_id,
                investigations.c.status.in_(ACTIVE_INVESTIGATION),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        return EnqueueResult(active, created=False, state="existing")
    return None


async def queue_depth(conn: AsyncConnection, org_id: UUID) -> dict[str, int]:
    rows = await conn.execute(
        select(jobs.c.status, func.count())
        .where(jobs.c.org_id == org_id, jobs.c.status.in_(("queued", "deferred", "running")))
        .group_by(jobs.c.status)
    )
    depth = {"queued": 0, "deferred": 0, "running": 0}
    depth.update({r[0]: int(r[1]) for r in rows})
    return depth


async def enqueue_investigation(
    conn: AsyncConnection,
    *,
    org_id: UUID,
    incident_id: UUID,
    windows: Windows,
    source: dict[str, Any],
    trigger: Literal["manual", "auto", "replay"],
    requested_by: str | None,
    idempotency_key: str | None,
    queue_limit: int,
    max_attempts: int,
) -> EnqueueResult:
    """Create an investigation and its job in the caller's transaction (no dual write).

    Idempotent: a repeated idempotency key, or an incident that already has an active
    investigation, returns the existing investigation instead of creating a second one.
    """
    automatic = trigger == "auto"
    found = await _existing(conn, org_id, incident_id, idempotency_key)
    if found is not None:
        return found
    depth = await queue_depth(conn, org_id)
    job_status = "queued"
    if depth["queued"] >= queue_limit:
        if not automatic:
            raise QueueFull(f"{depth['queued']} investigations queued (limit {queue_limit})")
        if depth["deferred"] >= queue_limit * DEFERRED_FACTOR:
            return EnqueueResult(None, created=False, state="skipped")
        job_status = "deferred"
    try:
        async with conn.begin_nested():
            inv_id = (
                await conn.execute(
                    insert(investigations)
                    .values(
                        org_id=org_id,
                        incident_id=incident_id,
                        trigger=trigger,
                        requested_by=requested_by,
                        idempotency_key=idempotency_key,
                        window_start=windows.incident_start,
                        window_end=windows.incident_end,
                        baseline_start=windows.baseline_start,
                        baseline_end=windows.baseline_end,
                        source=source,
                    )
                    .returning(investigations.c.id)
                )
            ).scalar_one()
            await conn.execute(
                insert(jobs).values(
                    org_id=org_id,
                    investigation_id=inv_id,
                    status=job_status,
                    priority=0 if automatic else 10,
                    max_attempts=max_attempts,
                )
            )
    except IntegrityError:
        # A concurrent request created the active investigation or used the key first.
        found = await _existing(conn, org_id, incident_id, idempotency_key)
        if found is None:
            raise
        return found
    if job_status == "deferred":
        await events.append(
            conn,
            org_id,
            inv_id,
            EventKind.DEFERRED,
            f"Queue limit reached ({depth['queued']} queued); investigation deferred, not dropped",
            state="queued",
            data={"queued": depth["queued"], "limit": queue_limit},
        )
        return EnqueueResult(inv_id, created=True, state="deferred")
    await events.append(
        conn,
        org_id,
        inv_id,
        EventKind.QUEUED,
        "Investigation queued",
        state="queued",
        actor=requested_by,
        data={"trigger": trigger},
    )
    return EnqueueResult(inv_id, created=True, state="queued")


# --- cancellation (API) ---------------------------------------------------------------------


async def lock_investigation(conn: AsyncConnection, org_id: UUID, investigation_id: UUID) -> Any:
    return (
        (
            await conn.execute(
                select(investigations)
                .where(investigations.c.org_id == org_id, investigations.c.id == investigation_id)
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )


async def request_cancel(
    conn: AsyncConnection, org_id: UUID, investigation_id: UUID, actor: str | None
) -> Literal["cancelled", "cancel_requested", "not_active", "not_found"]:
    inv = await lock_investigation(conn, org_id, investigation_id)
    if inv is None:
        return "not_found"
    if inv["status"] not in ACTIVE_INVESTIGATION:
        return "not_active"
    job = (
        await conn.execute(
            select(jobs.c.id, jobs.c.status)
            .where(jobs.c.org_id == org_id, jobs.c.investigation_id == investigation_id)
            .with_for_update()
        )
    ).first()
    if job is not None and job.status in ("queued", "deferred"):
        await conn.execute(
            update(jobs)
            .where(jobs.c.id == job.id)
            .values(status="cancelled", updated_at=func.now())
        )
        await conn.execute(
            update(investigations)
            .where(investigations.c.id == investigation_id)
            .values(status="cancelled", cancel_requested=True, finished_at=func.now())
        )
        await events.append(
            conn,
            org_id,
            investigation_id,
            EventKind.CANCELLED,
            "Investigation cancelled before it started",
            state="cancelled",
            actor=actor,
        )
        return "cancelled"
    await conn.execute(
        update(investigations)
        .where(investigations.c.id == investigation_id)
        .values(cancel_requested=True)
    )
    await events.append(
        conn,
        org_id,
        investigation_id,
        EventKind.CANCEL_REQUESTED,
        "Cancellation requested; the worker stops at the next stage boundary",
        state="running",
        actor=actor,
    )
    return "cancel_requested"


# --- worker side ------------------------------------------------------------------------------


async def claim(
    engine: AsyncEngine, worker: str, lease_seconds: int, org_cap: int
) -> ClaimedJob | None:
    async with system_tx(engine) as c:
        row = (
            await c.execute(
                text("SELECT * FROM aegis.claim_job(:w, :lease, :cap)"),
                {"w": worker, "lease": lease_seconds, "cap": org_cap},
            )
        ).first()
    if row is None:
        return None
    return ClaimedJob(row.job_id, row.org_id, row.investigation_id, row.attempt, worker)


def _fenced(job: ClaimedJob) -> Sequence[Any]:
    return (
        jobs.c.id == job.job_id,
        jobs.c.org_id == job.org_id,
        jobs.c.status == "running",
        jobs.c.lease_owner == job.worker,
        jobs.c.attempts == job.attempt,
    )


async def heartbeat(conn: AsyncConnection, job: ClaimedJob, lease_seconds: int) -> bool:
    row = (
        await conn.execute(
            update(jobs)
            .where(*_fenced(job))
            .values(
                lease_expires_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, lease_seconds),
                updated_at=func.now(),
            )
            .returning(jobs.c.id)
        )
    ).first()
    return row is not None


async def finish(
    conn: AsyncConnection,
    job: ClaimedJob,
    status: Literal["succeeded", "failed", "cancelled"],
    error: str | None = None,
) -> None:
    """Fenced terminal transition. Raises LeaseLost if this worker no longer owns the job."""
    row = (
        await conn.execute(
            update(jobs)
            .where(*_fenced(job))
            .values(
                status=status,
                last_error=error[:2000] if error else None,
                lease_expires_at=None,
                updated_at=func.now(),
            )
            .returning(jobs.c.id)
        )
    ).first()
    if row is None:
        raise LeaseLost(str(job.job_id))


async def schedule_retry(
    conn: AsyncConnection, job: ClaimedJob, error: str, delay_seconds: float
) -> None:
    row = (
        await conn.execute(
            update(jobs)
            .where(*_fenced(job))
            .values(
                status="queued",
                available_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, delay_seconds),
                lease_owner=None,
                lease_expires_at=None,
                last_error=error[:2000],
                updated_at=func.now(),
            )
            .returning(jobs.c.id)
        )
    ).first()
    if row is None:
        raise LeaseLost(str(job.job_id))


async def attempts_left(conn: AsyncConnection, job: ClaimedJob) -> int:
    max_attempts: int = (
        await conn.execute(select(jobs.c.max_attempts).where(jobs.c.id == job.job_id))
    ).scalar_one()
    return max_attempts - job.attempt


def retry_delay(
    attempt: int, *, base: float = 5.0, cap: float = 300.0, rng: random.Random | None = None
) -> float:
    """Jittered exponential backoff: base * 2^(attempt-1), capped, times U(0.5, 1.5)."""
    r = rng or random.SystemRandom()
    return float(min(cap, base * 2 ** max(0, attempt - 1)) * r.uniform(0.5, 1.5))


async def reap_dead(engine: AsyncEngine) -> list[tuple[UUID, UUID, UUID]]:
    async with system_tx(engine) as c:
        rows = (await c.execute(text("SELECT * FROM aegis.reap_dead_jobs()"))).all()
    return [(r.job_id, r.org_id, r.investigation_id) for r in rows]


async def promote_deferred(engine: AsyncEngine, queue_limit: int) -> list[tuple[UUID, UUID, UUID]]:
    async with system_tx(engine) as c:
        rows = (
            await c.execute(
                text("SELECT * FROM aegis.promote_deferred_jobs(:l)"), {"l": queue_limit}
            )
        ).all()
    return [(r.job_id, r.org_id, r.investigation_id) for r in rows]
