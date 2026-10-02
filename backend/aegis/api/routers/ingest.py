"""Authenticated inbound webhooks: alerts (Alertmanager v4, generic) and change events.

The organization is taken from the credential (``Principal.org_id``) — payloads have no way
to name one. Ingestion is rate limited per credential and bounded per payload; one payload
is processed in one transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import and_, or_, select

from aegis import audit
from aegis.api import pagination, views
from aegis.api import schemas as S
from aegis.api.deps import app_state, rate_limit, require
from aegis.api.routers.incidents import live_ready
from aegis.api.state import AppState
from aegis.audit import Action
from aegis.auth.principal import Principal
from aegis.auth.rbac import Permission
from aegis.db.engine import tenant_tx
from aegis.db.tables import change_events, organizations
from aegis.ingest import changes
from aegis.ingest.alerts import (
    AlertmanagerPayload,
    GenericPayload,
    NormalizedAlert,
    from_alertmanager,
    from_generic,
)
from aegis.ingest.service import ingest_alerts
from aegis.investigations.sources import LiveSource
from aegis.org_settings import read_settings

router = APIRouter(tags=["ingestion"])


async def _ingest(
    state: AppState, principal: Principal, batch: list[NormalizedAlert]
) -> S.IngestOut:
    rate_limit(
        state,
        f"ingest:{principal.label}",
        state.settings.ingest_per_minute,
        state.settings.ingest_burst,
    )
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        org_settings = read_settings(
            (
                await c.execute(
                    select(organizations.c.settings).where(organizations.c.id == principal.org_id)
                )
            ).scalar_one()
        )
        if org_settings.default_source is None and await live_ready(c, state, principal.org_id):
            org_settings = org_settings.model_copy(update={"default_source": LiveSource()})
        result = await ingest_alerts(
            c,
            org_id=principal.org_id,
            batch=batch,
            settings=org_settings,
            actor=principal.actor,
            via_key=principal.api_key_id,
            scenarios=state.scenarios,
            queue_limit=state.settings.org_queue_limit,
            max_attempts=state.settings.job_max_attempts,
        )
    return S.IngestOut(**result.as_dict())


@router.post("/alerts/alertmanager", response_model=S.IngestOut, status_code=202)
async def alertmanager_webhook(
    payload: AlertmanagerPayload,
    principal: Annotated[Principal, Depends(require(Permission.ALERT_INGEST))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.IngestOut:
    return await _ingest(state, principal, from_alertmanager(payload))


@router.post("/alerts", response_model=S.IngestOut, status_code=202)
async def generic_webhook(
    payload: GenericPayload,
    principal: Annotated[Principal, Depends(require(Permission.ALERT_INGEST))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.IngestOut:
    return await _ingest(state, principal, from_generic(payload))


@router.post("/change-events", response_model=S.ChangeIngestOut, status_code=201)
async def post_change_event(
    event: changes.ChangeEventIn,
    response: Response,
    principal: Annotated[Principal, Depends(require(Permission.CHANGE_WRITE))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.ChangeIngestOut:
    rate_limit(
        state,
        f"ingest:{principal.label}",
        state.settings.ingest_per_minute,
        state.settings.ingest_burst,
    )
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        change_id, created = await changes.store(c, principal.org_id, event, principal.api_key_id)
        if created:
            await audit.record(
                c,
                principal.org_id,
                principal.actor,
                Action.CHANGE_EVENT_INGESTED,
                target_type="change_event",
                target_id=change_id,
                details={"kind": event.kind, "service": event.service, "source": event.source},
            )
    if not created:
        response.status_code = 200
    return S.ChangeIngestOut(id=change_id, created=created)


@router.get("/change-events", response_model=S.Page[S.ChangeEventOut])
async def list_change_events(
    principal: Annotated[Principal, Depends(require(Permission.CHANGE_READ))],
    state: Annotated[AppState, Depends(app_state)],
    service: Annotated[str | None, Query(max_length=200)] = None,
    environment: Annotated[str | None, Query(max_length=100)] = None,
    kind: Annotated[
        list[Literal["deployment", "config", "feature_flag", "rollout", "infrastructure", "other"]]
        | None,
        Query(),
    ] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=pagination.MAX_LIMIT)] = pagination.DEFAULT_LIMIT,
    cursor: str | None = None,
) -> S.Page[S.ChangeEventOut]:
    stmt = select(change_events).where(change_events.c.org_id == principal.org_id)
    if service:
        stmt = stmt.where(change_events.c.service == service)
    if environment:
        stmt = stmt.where(change_events.c.environment == environment)
    if kind:
        stmt = stmt.where(change_events.c.kind.in_(kind))
    if since:
        stmt = stmt.where(change_events.c.ts >= since)
    if until:
        stmt = stmt.where(change_events.c.ts < until)
    after = pagination.decode(cursor, 2)
    if after:
        k, i = pagination.parse_dt(after[0]), UUID(str(after[1]))
        stmt = stmt.where(
            or_(change_events.c.ts < k, and_(change_events.c.ts == k, change_events.c.id < i))
        )
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rows = (
            (
                await c.execute(
                    stmt.order_by(change_events.c.ts.desc(), change_events.c.id.desc()).limit(
                        limit + 1
                    )
                )
            )
            .mappings()
            .all()
        )
    nxt = (
        pagination.encode(rows[limit - 1]["ts"], rows[limit - 1]["id"])
        if len(rows) > limit
        else None
    )
    return S.Page[S.ChangeEventOut](
        items=[views.change_event(r) for r in rows[:limit]], next_cursor=nxt
    )
