"""Investigation event history (feeds SSE and the polling endpoint).

``append`` increments ``investigations.event_seq`` under the row lock and inserts the event
with that number, so the worker and the API (cancel requests) can both append without
colliding on ``(investigation_id, seq)``. Event data are identifiers, counts and states —
never telemetry payloads.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from aegis.core.sanitize import single_line
from aegis.db.tables import investigation_events, investigations


class EventKind(StrEnum):
    QUEUED = "queued"
    DEFERRED = "deferred"
    CLAIMED = "claimed"
    COLLECTING = "collecting"
    COLLECTED = "collected"  # one per modality/provider outcome
    SNAPSHOT_STORED = "snapshot_stored"
    ANALYZING = "analyzing"
    HYPOTHESES = "hypotheses"
    DIAGNOSIS = "diagnosis"
    AI_ADJUDICATION = "ai_adjudication"
    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


TERMINAL_KINDS = frozenset({EventKind.COMPLETED, EventKind.FAILED, EventKind.CANCELLED})


async def append(
    conn: AsyncConnection,
    org_id: UUID,
    investigation_id: UUID,
    kind: EventKind,
    message: str,
    *,
    state: str | None = None,
    actor: str | None = None,
    data: dict[str, Any] | None = None,
) -> int:
    seq = (
        await conn.execute(
            update(investigations)
            .where(investigations.c.org_id == org_id, investigations.c.id == investigation_id)
            .values(event_seq=investigations.c.event_seq + 1)
            .returning(investigations.c.event_seq)
        )
    ).scalar_one()
    await conn.execute(
        insert(investigation_events).values(
            org_id=org_id,
            investigation_id=investigation_id,
            seq=seq,
            kind=kind.value,
            state=state,
            actor=single_line(actor)[:200] if actor else None,
            message=single_line(message)[:2000],
            data=data or {},
        )
    )
    return int(seq)


async def list_after(
    conn: AsyncConnection, org_id: UUID, investigation_id: UUID, after_seq: int, limit: int = 200
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            select(investigation_events)
            .where(
                investigation_events.c.org_id == org_id,
                investigation_events.c.investigation_id == investigation_id,
                investigation_events.c.seq > after_seq,
            )
            .order_by(investigation_events.c.seq)
            .limit(limit)
        )
    ).mappings()
    return [dict(r) for r in rows]
