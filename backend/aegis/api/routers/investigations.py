"""Investigations: detail, cancel, retry, events (polling and SSE), evidence, graph, snapshot.

Live progress (ADR-010): ``GET /investigations/{id}/stream`` is a Server-Sent Events stream
that polls the persisted ``investigation_events`` table. Each event carries its sequence
number as the SSE ``id``; a reconnecting client sends ``Last-Event-ID`` (or ``?after=``) and
receives everything after it, so no event is lost across reconnects. The stream ends after
a terminal event or after ``STREAM_MAX_SECONDS`` (the client reconnects, which also
re-checks its session). ``GET /investigations/{id}/events?after=`` serves the same data for
clients without SSE.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select

from aegis import audit
from aegis.api import pagination, views
from aegis.api import schemas as S
from aegis.api.deps import app_state, require
from aegis.api.errors import ApiError, not_found
from aegis.api.queries import get_investigation, investigation_select
from aegis.api.state import AppState
from aegis.audit import Action
from aegis.auth.principal import Principal
from aegis.auth.rbac import Permission
from aegis.db.engine import tenant_tx
from aegis.db.tables import (
    candidates,
    evidence,
    evidence_relations,
    investigations,
    root_causes,
    snapshots,
)
from aegis.jobs import events, queue
from aegis.jobs.events import TERMINAL_KINDS

router = APIRouter(prefix="/investigations", tags=["investigations"])

STREAM_MAX_SECONDS = 300
KEEPALIVE_SECONDS = 15


@router.get("", response_model=S.Page[S.InvestigationOut])
async def list_investigations(
    principal: Annotated[Principal, Depends(require(Permission.INVESTIGATION_READ))],
    state: Annotated[AppState, Depends(app_state)],
    status: Annotated[
        list[Literal["queued", "running", "completed", "failed", "cancelled"]] | None, Query()
    ] = None,
    incident_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=pagination.MAX_LIMIT)] = pagination.DEFAULT_LIMIT,
    cursor: str | None = None,
) -> S.Page[S.InvestigationOut]:
    stmt = investigation_select().where(investigations.c.org_id == principal.org_id)
    if status:
        stmt = stmt.where(investigations.c.status.in_(status))
    if incident_id:
        stmt = stmt.where(investigations.c.incident_id == incident_id)
    after = pagination.decode(cursor, 2)
    if after:
        k, i = pagination.parse_dt(after[0]), UUID(str(after[1]))
        stmt = stmt.where(
            or_(
                investigations.c.created_at < k,
                and_(investigations.c.created_at == k, investigations.c.id < i),
            )
        )
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rows = (
            (
                await c.execute(
                    stmt.order_by(
                        investigations.c.created_at.desc(), investigations.c.id.desc()
                    ).limit(limit + 1)
                )
            )
            .mappings()
            .all()
        )
    nxt = (
        pagination.encode(rows[limit - 1]["created_at"], rows[limit - 1]["id"])
        if len(rows) > limit
        else None
    )
    return S.Page[S.InvestigationOut](
        items=[views.investigation(r) for r in rows[:limit]], next_cursor=nxt
    )


@router.get("/{investigation_id}", response_model=S.InvestigationOut)
async def get_one(
    investigation_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INVESTIGATION_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.InvestigationOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = await get_investigation(c, principal.org_id, investigation_id)
    if row is None:
        raise not_found("Investigation")
    return views.investigation(row)


@router.post("/{investigation_id}/cancel", response_model=S.CancelOut, status_code=202)
async def cancel(
    investigation_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INVESTIGATION_CANCEL))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.CancelOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        result = await queue.request_cancel(c, principal.org_id, investigation_id, principal.label)
        if result == "not_found":
            raise not_found("Investigation")
        if result == "not_active":
            raise ApiError(409, "not_active", "The investigation is not queued or running")
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.INVESTIGATION_CANCELLED
            if result == "cancelled"
            else Action.INVESTIGATION_CANCEL_REQUESTED,
            target_type="investigation",
            target_id=investigation_id,
        )
    return S.CancelOut(investigation_id=investigation_id, result=result)


@router.post("/{investigation_id}/retry", response_model=S.InvestigationOut, status_code=201)
async def retry(
    investigation_id: UUID,
    response: Response,
    principal: Annotated[Principal, Depends(require(Permission.INVESTIGATION_START))],
    state: Annotated[AppState, Depends(app_state)],
    idempotency_key: Annotated[
        str | None, Header(max_length=200, pattern=r"^[A-Za-z0-9._:-]{1,200}$")
    ] = None,
) -> S.InvestigationOut:
    """Run the same investigation definition (source and windows) again as a new investigation."""
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        old = await get_investigation(c, principal.org_id, investigation_id)
        if old is None:
            raise not_found("Investigation")
        if old["status"] in queue.ACTIVE_INVESTIGATION:
            raise ApiError(409, "still_active", "The investigation is still queued or running")
        try:
            res = await queue.enqueue_investigation(
                c,
                org_id=principal.org_id,
                incident_id=old["incident_id"],
                windows=queue.Windows(
                    old["window_start"],
                    old["window_end"],
                    old["baseline_start"],
                    old["baseline_end"],
                ),
                source=dict(old["source"]),
                trigger="manual",
                requested_by=principal.label,
                idempotency_key=idempotency_key,
                queue_limit=state.settings.org_queue_limit,
                max_attempts=state.settings.job_max_attempts,
            )
        except queue.QueueFull as exc:
            raise ApiError(429, "queue_full", str(exc), headers={"Retry-After": "30"}) from None
        except queue.IdempotencyConflict:
            raise ApiError(
                409, "idempotency_conflict", "This Idempotency-Key was used for another incident"
            ) from None
        assert res.investigation_id is not None
        if res.created:
            await audit.record(
                c,
                principal.org_id,
                principal.actor,
                Action.INVESTIGATION_RETRIED,
                target_type="investigation",
                target_id=res.investigation_id,
                details={"retry_of": str(investigation_id)},
            )
        row = await get_investigation(c, principal.org_id, res.investigation_id)
    if not res.created:
        response.status_code = 200
    return views.investigation(row)


async def _assert_exists(state: AppState, principal: Principal, investigation_id: UUID) -> Any:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            await c.execute(
                select(
                    investigations.c.id, investigations.c.status, investigations.c.event_seq
                ).where(
                    investigations.c.org_id == principal.org_id,
                    investigations.c.id == investigation_id,
                )
            )
        ).first()
    if row is None:
        raise not_found("Investigation")
    return row


@router.get("/{investigation_id}/events", response_model=list[S.EventOut])
async def list_events(
    investigation_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INVESTIGATION_READ))],
    state: Annotated[AppState, Depends(app_state)],
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[S.EventOut]:
    await _assert_exists(state, principal, investigation_id)
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rows = await events.list_after(c, principal.org_id, investigation_id, after, limit)
    return [views.event(r) for r in rows]


def _sse(row: dict[str, Any]) -> str:
    payload = views.event(row).model_dump(mode="json")
    # json.dumps escapes newlines, so telemetry text cannot inject SSE fields
    return f"id: {row['seq']}\nevent: {row['kind']}\ndata: {json.dumps(payload)}\n\n"


@router.get("/{investigation_id}/stream", response_class=StreamingResponse)
async def stream(
    investigation_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require(Permission.INVESTIGATION_READ))],
    state: Annotated[AppState, Depends(app_state)],
    after: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header(max_length=20)] = None,
) -> StreamingResponse:
    inv = await _assert_exists(state, principal, investigation_id)
    resume = last_event_id or ""
    start = after if after is not None else (int(resume) if resume.isdigit() else 0)
    stream_key = principal.label
    if state.open_streams[stream_key] >= state.settings.sse_max_streams_per_user:
        raise ApiError(429, "too_many_streams", "Too many open event streams")
    terminal_status = inv.status in queue.TERMINAL_INVESTIGATION

    async def gen() -> AsyncIterator[str]:
        # Counted inside the generator: if the client disconnects before the body starts,
        # the generator never runs and nothing is left counted (no slot leak).
        state.open_streams[stream_key] += 1
        seq = start
        deadline = time.monotonic() + STREAM_MAX_SECONDS
        last_sent = time.monotonic()
        try:
            yield "retry: 3000\n\n"
            while time.monotonic() < deadline:
                if await request.is_disconnected():
                    return
                async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
                    rows = await events.list_after(c, principal.org_id, investigation_id, seq)
                done = False
                for r in rows:
                    seq = r["seq"]
                    yield _sse(r)
                    last_sent = time.monotonic()
                    done = done or r["kind"] in TERMINAL_KINDS
                if done or (terminal_status and not rows):
                    return
                if time.monotonic() - last_sent > KEEPALIVE_SECONDS:
                    yield ": keepalive\n\n"
                    last_sent = time.monotonic()
                await asyncio.sleep(state.settings.sse_poll_seconds)
        finally:
            state.open_streams[stream_key] -= 1
            if state.open_streams[stream_key] <= 0:
                del state.open_streams[stream_key]

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/{investigation_id}/evidence", response_model=S.Page[S.EvidenceOut])
async def list_evidence(
    investigation_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.EVIDENCE_READ))],
    state: Annotated[AppState, Depends(app_state)],
    kind: Annotated[list[str] | None, Query()] = None,
    modality: Annotated[list[str] | None, Query()] = None,
    service: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=pagination.MAX_LIMIT)] = 100,
    cursor: str | None = None,
) -> S.Page[S.EvidenceOut]:
    await _assert_exists(state, principal, investigation_id)
    stmt = select(evidence).where(
        evidence.c.org_id == principal.org_id, evidence.c.investigation_id == investigation_id
    )
    if kind:
        stmt = stmt.where(evidence.c.kind.in_(kind[:20]))
    if modality:
        stmt = stmt.where(evidence.c.modality.in_(modality[:10]))
    if service:
        stmt = stmt.where(evidence.c.service == service)
    after = pagination.decode(cursor, 1)
    if after:
        stmt = stmt.where(evidence.c.evidence_id > (after[0] or ""))
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rows = (
            (await c.execute(stmt.order_by(evidence.c.evidence_id).limit(limit + 1)))
            .mappings()
            .all()
        )
    nxt = pagination.encode(rows[limit - 1]["evidence_id"]) if len(rows) > limit else None
    return S.Page[S.EvidenceOut](items=[views.evidence(r) for r in rows[:limit]], next_cursor=nxt)


@router.get("/{investigation_id}/evidence/{evidence_id}", response_model=S.EvidenceOut)
async def get_evidence(
    investigation_id: UUID,
    evidence_id: str,
    principal: Annotated[Principal, Depends(require(Permission.EVIDENCE_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.EvidenceOut:
    if len(evidence_id) > 100:
        raise not_found("Evidence")
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            (
                await c.execute(
                    select(evidence).where(
                        evidence.c.org_id == principal.org_id,
                        evidence.c.investigation_id == investigation_id,
                        evidence.c.evidence_id == evidence_id,
                    )
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise not_found("Evidence")
    return views.evidence(row)


@router.get("/{investigation_id}/graph", response_model=S.GraphOut)
async def graph(
    investigation_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.EVIDENCE_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.GraphOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        inv = await get_investigation(c, principal.org_id, investigation_id)
        if inv is None:
            raise not_found("Investigation")
        key = (principal.org_id, investigation_id)

        def scoped(t: Any) -> Any:
            return select(t).where(t.c.org_id == key[0], t.c.investigation_id == key[1])

        cands = (await c.execute(scoped(candidates).order_by(candidates.c.rank))).mappings().all()
        roots = (
            (await c.execute(scoped(root_causes).order_by(root_causes.c.service))).mappings().all()
        )
        rels = (await c.execute(scoped(evidence_relations).limit(5000))).mappings().all()
    verdict = inv["verdict"] or {}
    return S.GraphOut(
        investigation_id=investigation_id,
        candidates=[views.candidate(r) for r in cands],
        root_causes=[
            S.RootCauseOut(
                service=r["service"],
                mechanism=r["mechanism"],
                support=r["support"],
                supporting_evidence_ids=list(r["supporting"] or []),
                contradicting_evidence_ids=list(r["contradicting"] or []),
            )
            for r in roots
        ],
        relations=[
            S.RelationOut(source=r["source"], target=r["target"], kind=r["kind"]) for r in rels
        ],
        decision_trace=list(verdict.get("decision_trace", [])),
    )


@router.get("/{investigation_id}/snapshot", response_model=S.SnapshotSummaryOut)
async def snapshot_summary(
    investigation_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.EVIDENCE_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.SnapshotSummaryOut:
    """Collection outcomes and counts — not the raw telemetry payload."""
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            (
                await c.execute(
                    select(
                        snapshots.c.digest,
                        snapshots.c.synthetic,
                        snapshots.c.schema_version,
                        snapshots.c.redaction,
                        snapshots.c.payload["captured_at"].astext.label("captured_at"),
                        snapshots.c.payload["collected_window"].label("collected_window"),
                        snapshots.c.payload["collection"].label("collection"),
                        *(
                            func.jsonb_array_length(snapshots.c.payload[k]).label(k)
                            for k in ("metrics", "logs", "spans", "changes")
                        ),
                    ).where(
                        snapshots.c.org_id == principal.org_id,
                        snapshots.c.investigation_id == investigation_id,
                    )
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise not_found("Snapshot")
    window = row["collected_window"] or None
    return S.SnapshotSummaryOut(
        investigation_id=investigation_id,
        digest=row["digest"],
        synthetic=row["synthetic"],
        schema_version=row["schema_version"],
        captured_at=row["captured_at"],
        collected_window=S.WindowOut(**window) if window else None,
        counts={k: int(row[k] or 0) for k in ("metrics", "logs", "spans", "changes")},
        collection=[S.CollectionOut(**o) for o in (row["collection"] or [])],
        redaction=row["redaction"] or {},
    )
