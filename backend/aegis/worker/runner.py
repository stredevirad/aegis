"""The investigation worker (ADR-010).

Each slot loop claims one job at a time and runs the pipeline::

    claim → load investigation → snapshot (reuse stored, else collect) → persist snapshot
          → analyze (pure core, in a thread) → persist graph + verdict + complete (one fenced
          transaction) → events

Every stage appends an ``investigation_events`` row (the SSE feed). Cancellation is checked
between stages. A heartbeat task extends the lease; if the lease is lost (another worker
reclaimed the job after this one stalled), the pipeline task is cancelled and every later
write is refused by the fence. Errors are classified: ``RetryableError`` and database
connectivity errors are retried with jittered backoff while attempts remain; everything else
fails the investigation permanently with a message that contains no telemetry or secrets.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from aegis import audit
from aegis.audit import Action, Actor
from aegis.config import Settings
from aegis.core import analyze
from aegis.core.model import AnalysisRequest, TelemetrySnapshot, TimeWindow
from aegis.core.result import digest_of
from aegis.db.engine import tenant_tx
from aegis.db.tables import incidents, investigations
from aegis.investigations import persist
from aegis.investigations.sources import ScenarioRegistry, SourceUnavailable
from aegis.investigations.windows import previous_day
from aegis.jobs import events, queue
from aegis.jobs.events import EventKind
from aegis.jobs.queue import ClaimedJob, LeaseLost, RetryableError, Windows
from aegis.observability.context import investigation_id_var, job_id_var, org_id_var

log = logging.getLogger("aegis.worker")


class InvestigationCancelled(Exception):
    pass


class PermanentError(Exception):
    """A failure that retrying cannot fix; the message is safe to show to operators."""


_RETRYABLE_DB = (OperationalError, InterfaceError)


def safe_error(exc: BaseException) -> str:
    """Operator-facing error text: never raw exception messages from external systems."""
    if isinstance(exc, (PermanentError, SourceUnavailable, RetryableError)):
        return str(exc)[:500]
    return f"internal error ({type(exc).__name__}); see worker logs for this job"


@dataclass
class _JobState:
    job: ClaimedJob
    lease_lost: asyncio.Event = field(default_factory=asyncio.Event)


class Worker:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        scenarios: ScenarioRegistry,
        worker_id: str | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.scenarios = scenarios
        self.worker_id = (
            worker_id or settings.worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        )
        self.poll_seconds = poll_seconds
        self.processed: Counter[str] = Counter()

    # --- process loop ---------------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        slots = [
            asyncio.create_task(self._slot(stop), name=f"slot-{i}")
            for i in range(self.settings.worker_concurrency)
        ]
        maintenance = asyncio.create_task(self._maintenance(stop), name="maintenance")
        await stop.wait()
        await asyncio.gather(*slots, return_exceptions=True)
        maintenance.cancel()
        await asyncio.gather(maintenance, return_exceptions=True)

    async def _slot(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                job = await queue.claim(
                    self.engine,
                    self.worker_id,
                    self.settings.job_lease_seconds,
                    self.settings.org_running_cap,
                )
            except (DBAPIError, OSError):
                log.exception("claim failed")
                job = None
            if job is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
                continue
            await self.process(job)

    async def _maintenance(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.maintenance_once()
            except (DBAPIError, OSError):
                log.exception("maintenance failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=10)

    async def maintenance_once(self) -> None:
        for job_id, org_id, inv_id in await queue.reap_dead(self.engine):
            log.warning("reaped dead job", extra={"job_id": str(job_id), "org_id": str(org_id)})
            async with tenant_tx(self.engine, org_id) as c:
                await audit.record(
                    c,
                    org_id,
                    Actor.system("reaper"),
                    Action.INVESTIGATION_FAILED,
                    target_type="investigation",
                    target_id=inv_id,
                    details={
                        "reason": "lease expired with no attempts left",
                        "job_id": str(job_id),
                    },
                )
        for _job_id, org_id, inv_id in await queue.promote_deferred(
            self.engine, self.settings.org_queue_limit
        ):
            async with tenant_tx(self.engine, org_id) as c:
                await events.append(
                    c,
                    org_id,
                    inv_id,
                    EventKind.QUEUED,
                    "Deferred investigation queued",
                    state="queued",
                    actor="scheduler",
                )

    # --- one job --------------------------------------------------------------------------

    async def process(self, job: ClaimedJob) -> str:
        """Run one claimed job to a terminal or retry state. Returns the outcome label."""
        tokens = (
            job_id_var.set(str(job.job_id)),
            org_id_var.set(str(job.org_id)),
            investigation_id_var.set(str(job.investigation_id)),
        )
        state = _JobState(job)
        pipeline = asyncio.create_task(self._pipeline(state))
        beat = asyncio.create_task(self._heartbeat(state, pipeline))
        outcome = "unknown"
        try:
            outcome = await pipeline
        except asyncio.CancelledError:
            if not state.lease_lost.is_set():
                raise
            outcome = "lease_lost"
        except LeaseLost:
            outcome = "lease_lost"
        except InvestigationCancelled:
            outcome = await self._finalize_cancel(job)
        except Exception as exc:
            outcome = await self._finalize_error(job, exc)
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
            for var, tok in zip(
                (job_id_var, org_id_var, investigation_id_var), tokens, strict=True
            ):
                var.reset(tok)
        if outcome == "lease_lost":
            log.warning(
                "lease lost; another worker owns the job", extra={"job_id": str(job.job_id)}
            )
        self.processed[outcome] += 1
        return outcome

    async def _heartbeat(self, state: _JobState, pipeline: asyncio.Task[str]) -> None:
        while True:
            await asyncio.sleep(self.settings.job_heartbeat_seconds)
            try:
                async with tenant_tx(self.engine, state.job.org_id) as c:
                    ok = await queue.heartbeat(c, state.job, self.settings.job_lease_seconds)
            except (DBAPIError, OSError):
                log.exception("heartbeat failed; will retry")
                continue
            if not ok:
                state.lease_lost.set()
                pipeline.cancel()
                return

    async def _check_cancel(self, job: ClaimedJob) -> None:
        async with tenant_tx(self.engine, job.org_id) as c:
            inv = await queue.lock_investigation(c, job.org_id, job.investigation_id)
            if inv is not None and inv["cancel_requested"]:
                raise InvestigationCancelled()

    async def _pipeline(self, state: _JobState) -> str:
        job = state.job
        org, inv_id = job.org_id, job.investigation_id
        # 1. start
        async with tenant_tx(self.engine, org) as c:
            inv = await queue.lock_investigation(c, org, inv_id)
            if inv is None:
                raise PermanentError("investigation no longer exists")
            if inv["status"] in queue.TERMINAL_INVESTIGATION:
                await queue.finish(
                    c, job, "cancelled" if inv["status"] == "cancelled" else "succeeded"
                )
                return "already_terminal"
            if inv["cancel_requested"]:
                raise InvestigationCancelled()
            await c.execute(
                update(investigations)
                .where(investigations.c.org_id == org, investigations.c.id == inv_id)
                .values(
                    status="running",
                    started_at=func.coalesce(investigations.c.started_at, func.now()),
                )
            )
            await events.append(
                c,
                org,
                inv_id,
                EventKind.CLAIMED,
                f"Claimed by worker (attempt {job.attempt})",
                state="running",
                actor=self.worker_id,
                data={"attempt": job.attempt},
            )
            source = dict(inv["source"])
            windows = Windows(
                inv["window_start"], inv["window_end"], inv["baseline_start"], inv["baseline_end"]
            )
            incident_service = (
                await c.execute(
                    select(incidents.c.service).where(
                        incidents.c.org_id == org, incidents.c.id == inv["incident_id"]
                    )
                )
            ).scalar_one_or_none()

        # 2. snapshot: reuse a stored one (resumable), otherwise collect
        async with tenant_tx(self.engine, org) as c:
            snapshot = await persist.load_snapshot(c, org, inv_id)
        if snapshot is not None:
            async with tenant_tx(self.engine, org) as c:
                await events.append(
                    c,
                    org,
                    inv_id,
                    EventKind.SNAPSHOT_STORED,
                    "Reusing the snapshot stored by an earlier attempt",
                    state="running",
                )
            request = self._request(source, windows, incident_service)
        else:
            await self._check_cancel(job)
            async with tenant_tx(self.engine, org) as c:
                await events.append(
                    c, org, inv_id, EventKind.COLLECTING, "Collecting telemetry", state="running"
                )
            snapshot, request = await self._collect(source, windows, incident_service)
            digest = digest_of(snapshot.model_dump(mode="json"))
            async with tenant_tx(self.engine, org) as c:
                if not await queue.heartbeat(c, job, self.settings.job_lease_seconds):
                    raise LeaseLost(str(job.job_id))
                for outcome in snapshot.collection:
                    await events.append(
                        c,
                        org,
                        inv_id,
                        EventKind.COLLECTED,
                        f"{outcome.modality.value} from {outcome.provider}: {outcome.status.value}",
                        state="running",
                        data={
                            "modality": outcome.modality.value,
                            "provider": outcome.provider,
                            "status": outcome.status.value,
                            "items": outcome.items,
                            "truncated": outcome.truncated,
                        },
                    )
                await persist.store_snapshot(
                    c,
                    org,
                    inv_id,
                    snapshot,
                    digest,
                    redaction={"applied": False, "reason": "synthetic source"}
                    if snapshot.synthetic
                    else {},
                )
                await events.append(
                    c,
                    org,
                    inv_id,
                    EventKind.SNAPSHOT_STORED,
                    "Telemetry snapshot stored",
                    state="running",
                    data={
                        "digest": digest,
                        "synthetic": snapshot.synthetic,
                        "series": len(snapshot.metrics),
                        "logs": len(snapshot.logs),
                        "spans": len(snapshot.spans),
                        "changes": len(snapshot.changes),
                    },
                )
                await audit.record(
                    c,
                    org,
                    Actor.system(self.worker_id),
                    Action.EVIDENCE_COLLECTED,
                    target_type="investigation",
                    target_id=inv_id,
                    details={"digest": digest, "synthetic": snapshot.synthetic},
                )

        # 3. deterministic analysis (pure, CPU-bound: off the event loop)
        await self._check_cancel(job)
        async with tenant_tx(self.engine, org) as c:
            await events.append(
                c,
                org,
                inv_id,
                EventKind.ANALYZING,
                "Running deterministic analysis",
                state="running",
            )
        analysis = await asyncio.to_thread(analyze, snapshot, request)

        # 4. persist results and complete, all-or-nothing under the fence
        async with tenant_tx(self.engine, org) as c:
            inv = await queue.lock_investigation(c, org, inv_id)
            if inv is None:
                raise PermanentError("investigation no longer exists")
            if inv["cancel_requested"]:
                raise InvestigationCancelled()
            await queue.finish(c, job, "succeeded")
            await persist.store_analysis(c, org, inv_id, analysis)
            leaders = [cand.service for cand in analysis.candidates if cand.rule is not None]
            await events.append(
                c,
                org,
                inv_id,
                EventKind.HYPOTHESES,
                f"{len(analysis.candidates)} candidate(s), {len(leaders)} leading",
                state="running",
                data={
                    "candidates": len(analysis.candidates),
                    "leaders": leaders[:20],
                    "evidence": len(analysis.evidence),
                },
            )
            v = analysis.verdict
            await events.append(
                c,
                org,
                inv_id,
                EventKind.DIAGNOSIS,
                v.display_label,
                state="running",
                data={
                    "classification": v.classification.value,
                    "support": v.support.value,
                    "root_causes": [rc.service for rc in v.root_causes],
                },
            )
            await events.append(
                c, org, inv_id, EventKind.COMPLETED, "Investigation completed", state="completed"
            )
            await audit.record(
                c,
                org,
                Actor.system(self.worker_id),
                Action.INVESTIGATION_COMPLETED,
                target_type="investigation",
                target_id=inv_id,
                details={"classification": v.classification.value, "support": v.support.value},
            )
        return "succeeded"

    def _request(
        self, source: dict[str, Any], windows: Windows, focus: str | None
    ) -> AnalysisRequest:
        if source.get("kind") == "scenario":
            return self.scenarios.request(str(source.get("scenario")))
        prev_start, prev_end = previous_day(windows)
        return AnalysisRequest(
            incident=TimeWindow(start=windows.incident_start, end=windows.incident_end),
            baselines=[
                TimeWindow(start=windows.baseline_start, end=windows.baseline_end),
                TimeWindow(start=prev_start, end=prev_end),
            ],
            focus_services=[focus] if focus else [],
        )

    async def _collect(
        self, source: dict[str, Any], windows: Windows, focus: str | None
    ) -> tuple[TelemetrySnapshot, AnalysisRequest]:
        kind = source.get("kind")
        if kind == "scenario":
            return await asyncio.to_thread(self.scenarios.snapshot, str(source.get("scenario")))
        if kind == "live":
            raise SourceUnavailable(
                "live telemetry collection is not configured in this deployment"
            )
        raise PermanentError(f"unknown snapshot source kind {kind!r}")

    # --- terminal transitions ------------------------------------------------------------

    async def _finalize_cancel(self, job: ClaimedJob) -> str:
        try:
            async with tenant_tx(self.engine, job.org_id) as c:
                await queue.lock_investigation(c, job.org_id, job.investigation_id)
                await queue.finish(c, job, "cancelled")
                await c.execute(
                    update(investigations)
                    .where(
                        investigations.c.org_id == job.org_id,
                        investigations.c.id == job.investigation_id,
                    )
                    .values(status="cancelled", finished_at=func.now())
                )
                await events.append(
                    c,
                    job.org_id,
                    job.investigation_id,
                    EventKind.CANCELLED,
                    "Investigation cancelled",
                    state="cancelled",
                    actor=self.worker_id,
                )
                await audit.record(
                    c,
                    job.org_id,
                    Actor.system(self.worker_id),
                    Action.INVESTIGATION_CANCELLED,
                    target_type="investigation",
                    target_id=job.investigation_id,
                )
        except LeaseLost:
            return "lease_lost"
        return "cancelled"

    async def _finalize_error(self, job: ClaimedJob, exc: Exception) -> str:
        retryable = isinstance(exc, (RetryableError, *_RETRYABLE_DB))
        message = safe_error(exc)
        log.error("job failed", exc_info=exc, extra={"retryable": retryable})
        try:
            async with tenant_tx(self.engine, job.org_id) as c:
                await queue.lock_investigation(c, job.org_id, job.investigation_id)
                left = await queue.attempts_left(c, job)
                if retryable and left > 0:
                    delay = queue.retry_delay(job.attempt)
                    await queue.schedule_retry(c, job, message, delay)
                    await c.execute(
                        update(investigations)
                        .where(
                            investigations.c.org_id == job.org_id,
                            investigations.c.id == job.investigation_id,
                        )
                        .values(status="queued")
                    )
                    await events.append(
                        c,
                        job.org_id,
                        job.investigation_id,
                        EventKind.RETRY_SCHEDULED,
                        f"Attempt {job.attempt} failed; retrying in {delay:.0f}s",
                        state="queued",
                        data={"error": message, "attempts_left": left},
                    )
                    return "retry_scheduled"
                await queue.finish(c, job, "failed", message)
                await c.execute(
                    update(investigations)
                    .where(
                        investigations.c.org_id == job.org_id,
                        investigations.c.id == job.investigation_id,
                    )
                    .values(status="failed", error=message, finished_at=func.now())
                )
                await events.append(
                    c,
                    job.org_id,
                    job.investigation_id,
                    EventKind.FAILED,
                    message,
                    state="failed",
                    data={"retryable": retryable, "attempt": job.attempt},
                )
                await audit.record(
                    c,
                    job.org_id,
                    Actor.system(self.worker_id),
                    Action.INVESTIGATION_FAILED,
                    target_type="investigation",
                    target_id=job.investigation_id,
                    details={"error": message},
                )
        except LeaseLost:
            return "lease_lost"
        except (DBAPIError, OSError):
            # The database is unreachable: leave the job; its lease expires and it is
            # reclaimed (or reaped when no attempts remain).
            log.exception("could not record job failure")
            return "unrecorded_failure"
        return "failed"
