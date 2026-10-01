"""Incidents: list/filter, manual creation, lifecycle updates, alerts, starting investigations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import and_, func, or_, select, update

from aegis import audit
from aegis.api import pagination, views
from aegis.api import schemas as S
from aegis.api.deps import app_state, require
from aegis.api.errors import ApiError, not_found
from aegis.api.queries import get_incident, get_investigation, investigation_select
from aegis.api.state import AppState
from aegis.audit import Action
from aegis.auth.principal import Principal
from aegis.auth.rbac import Permission
from aegis.core.sanitize import clean_name, clean_text
from aegis.db.engine import tenant_tx
from aegis.db.tables import alerts, incidents, investigations, organizations
from aegis.investigations import windows as win
from aegis.investigations.sources import LiveSource, ScenarioSource, SourceUnavailable
from aegis.jobs import queue
from aegis.org_settings import read_settings

router = APIRouter(prefix="/incidents", tags=["incidents"])

SORTS = {
    "-created_at": (incidents.c.created_at, True),
    "created_at": (incidents.c.created_at, False),
    "-started_at": (incidents.c.started_at, True),
    "started_at": (incidents.c.started_at, False),
}
# Allowed lifecycle transitions (manual). Closing or resolving also closes alert grouping.
TRANSITIONS: dict[str, set[str]] = {
    "open": {"investigating", "resolved", "closed"},
    "investigating": {"open", "resolved", "closed"},
    "resolved": {"open", "closed"},
    "closed": set(),
}
MAX_WINDOW = timedelta(hours=6)


@router.get("", response_model=S.Page[S.IncidentOut])
async def list_incidents(
    principal: Annotated[Principal, Depends(require(Permission.INCIDENT_READ))],
    state: Annotated[AppState, Depends(app_state)],
    status: Annotated[list[S.IncidentStatus] | None, Query()] = None,
    severity: Annotated[list[S.Severity] | None, Query()] = None,
    service: Annotated[str | None, Query(max_length=200)] = None,
    environment: Annotated[str | None, Query(max_length=100)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["-created_at", "created_at", "-started_at", "started_at"] = "-created_at",
    limit: Annotated[int, Query(ge=1, le=pagination.MAX_LIMIT)] = pagination.DEFAULT_LIMIT,
    cursor: str | None = None,
) -> S.Page[S.IncidentOut]:
    col, desc = SORTS[sort]
    stmt = select(incidents).where(incidents.c.org_id == principal.org_id)
    if status:
        stmt = stmt.where(incidents.c.status.in_(status))
    if severity:
        stmt = stmt.where(incidents.c.severity.in_(severity))
    if service:
        stmt = stmt.where(incidents.c.service == service)
    if environment:
        stmt = stmt.where(incidents.c.environment == environment)
    if since:
        stmt = stmt.where(incidents.c.started_at >= since)
    if until:
        stmt = stmt.where(incidents.c.started_at < until)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(incidents.c.title.ilike(f"%{escaped}%", escape="\\"))
    after = pagination.decode(cursor, 2)
    if after:
        k, i = pagination.parse_dt(after[0]), UUID(str(after[1]))
        stmt = stmt.where(
            or_(col < k, and_(col == k, incidents.c.id < i))
            if desc
            else or_(col > k, and_(col == k, incidents.c.id > i))
        )
    stmt = stmt.order_by(
        col.desc() if desc else col.asc(), incidents.c.id.desc() if desc else incidents.c.id.asc()
    )
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rows = (await c.execute(stmt.limit(limit + 1))).mappings().all()
    items = [views.incident(r) for r in rows[:limit]]
    nxt = None
    if len(rows) > limit:
        last = rows[limit - 1]
        nxt = pagination.encode(last[col.name], last["id"])
    return S.Page[S.IncidentOut](items=items, next_cursor=nxt)


@router.post("", response_model=S.IncidentOut, status_code=201)
async def create_incident(
    body: S.IncidentCreate,
    principal: Annotated[Principal, Depends(require(Permission.INCIDENT_WRITE))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.IncidentOut:
    now = datetime.now(UTC)
    started = min(body.started_at or now, now)
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            (
                await c.execute(
                    incidents.insert()
                    .values(
                        org_id=principal.org_id,
                        title=clean_name(body.title),
                        description=clean_text(body.description),
                        severity=body.severity,
                        service=clean_name(body.service) if body.service else None,
                        environment=clean_name(body.environment) if body.environment else None,
                        source="manual",
                        started_at=started,
                        created_by=principal.label,
                    )
                    .returning(incidents)
                )
            )
            .mappings()
            .one()
        )
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.INCIDENT_CREATED,
            target_type="incident",
            target_id=row["id"],
            details={"source": "manual", "severity": body.severity},
        )
    return views.incident(row)


@router.get("/{incident_id}", response_model=S.IncidentDetailOut)
async def get_incident_detail(
    incident_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INCIDENT_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.IncidentDetailOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = await get_incident(c, principal.org_id, incident_id)
        if row is None:
            raise not_found("Incident")
        count = (
            await c.execute(
                select(func.count())
                .select_from(investigations)
                .where(
                    investigations.c.org_id == principal.org_id,
                    investigations.c.incident_id == incident_id,
                )
            )
        ).scalar_one()
        latest = (
            (
                await c.execute(
                    investigation_select()
                    .where(
                        investigations.c.org_id == principal.org_id,
                        investigations.c.incident_id == incident_id,
                    )
                    .order_by(investigations.c.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    base = views.incident(row)
    return S.IncidentDetailOut(
        **base.model_dump(),
        investigations=count,
        latest_investigation=views.investigation(latest) if latest else None,
    )


@router.patch("/{incident_id}", response_model=S.IncidentOut)
async def update_incident(
    incident_id: UUID,
    body: S.IncidentUpdate,
    principal: Annotated[Principal, Depends(require(Permission.INCIDENT_WRITE))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.IncidentOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            (
                await c.execute(
                    select(incidents)
                    .where(incidents.c.org_id == principal.org_id, incidents.c.id == incident_id)
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("Incident")
        values: dict[str, Any] = {}
        changes: dict[str, Any] = {}
        if body.title is not None and body.title != row["title"]:
            values["title"] = clean_name(body.title)
        if body.severity is not None and body.severity != row["severity"]:
            values["severity"] = body.severity
        if body.status is not None and body.status != row["status"]:
            if body.status not in TRANSITIONS[row["status"]]:
                raise ApiError(
                    409,
                    "invalid_transition",
                    f"An incident cannot move from {row['status']} to {body.status}",
                )
            values["status"] = body.status
            if body.status in ("resolved", "closed"):
                values["group_key"] = None
                values["resolved_at"] = row["resolved_at"] or datetime.now(UTC)
            elif body.status == "open":
                values["resolved_at"] = None
        for k, v in values.items():
            if k not in ("group_key", "resolved_at"):
                changes[k] = {"from": row[k], "to": v}
        if not values:
            return views.incident(row)
        values["updated_at"] = func.now()
        updated = (
            (
                await c.execute(
                    update(incidents)
                    .where(incidents.c.org_id == principal.org_id, incidents.c.id == incident_id)
                    .values(**values)
                    .returning(incidents)
                )
            )
            .mappings()
            .one()
        )
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.INCIDENT_UPDATED,
            target_type="incident",
            target_id=incident_id,
            details={"changes": changes},
        )
    return views.incident(updated)


@router.get("/{incident_id}/alerts", response_model=S.Page[S.AlertOut])
async def list_alerts(
    incident_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INCIDENT_READ))],
    state: Annotated[AppState, Depends(app_state)],
    limit: Annotated[int, Query(ge=1, le=pagination.MAX_LIMIT)] = pagination.DEFAULT_LIMIT,
    cursor: str | None = None,
) -> S.Page[S.AlertOut]:
    stmt = select(alerts).where(
        alerts.c.org_id == principal.org_id, alerts.c.incident_id == incident_id
    )
    after = pagination.decode(cursor, 2)
    if after:
        k, i = pagination.parse_dt(after[0]), UUID(str(after[1]))
        stmt = stmt.where(
            or_(alerts.c.starts_at < k, and_(alerts.c.starts_at == k, alerts.c.id < i))
        )
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        if await get_incident(c, principal.org_id, incident_id) is None:
            raise not_found("Incident")
        rows = (
            (
                await c.execute(
                    stmt.order_by(alerts.c.starts_at.desc(), alerts.c.id.desc()).limit(limit + 1)
                )
            )
            .mappings()
            .all()
        )
    nxt = (
        pagination.encode(rows[limit - 1]["starts_at"], rows[limit - 1]["id"])
        if len(rows) > limit
        else None
    )
    return S.Page[S.AlertOut](items=[views.alert(r) for r in rows[:limit]], next_cursor=nxt)


@router.get("/{incident_id}/investigations", response_model=list[S.InvestigationOut])
async def list_incident_investigations(
    incident_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INVESTIGATION_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> list[S.InvestigationOut]:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        if await get_incident(c, principal.org_id, incident_id) is None:
            raise not_found("Incident")
        rows = (
            (
                await c.execute(
                    investigation_select()
                    .where(
                        investigations.c.org_id == principal.org_id,
                        investigations.c.incident_id == incident_id,
                    )
                    .order_by(investigations.c.created_at.desc())
                    .limit(100)
                )
            )
            .mappings()
            .all()
        )
    return [views.investigation(r) for r in rows]


@router.post("/{incident_id}/investigations", response_model=S.InvestigationOut, status_code=201)
async def start_investigation(
    incident_id: UUID,
    response: Response,
    principal: Annotated[Principal, Depends(require(Permission.INVESTIGATION_START))],
    state: Annotated[AppState, Depends(app_state)],
    body: S.InvestigationStart | None = None,
    idempotency_key: Annotated[
        str | None, Header(max_length=200, pattern=r"^[A-Za-z0-9._:-]{1,200}$")
    ] = None,
) -> S.InvestigationOut:
    body = body or S.InvestigationStart()
    now = datetime.now(UTC)
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        inc = await get_incident(c, principal.org_id, incident_id)
        if inc is None:
            raise not_found("Incident")
        org_settings = read_settings(
            (
                await c.execute(
                    select(organizations.c.settings).where(organizations.c.id == principal.org_id)
                )
            ).scalar_one_or_none()
        )
        source = body.source or org_settings.default_source or LiveSource()
        windows = resolve_windows(state, source, body.window, inc["started_at"], now)
        try:
            res = await queue.enqueue_investigation(
                c,
                org_id=principal.org_id,
                incident_id=incident_id,
                windows=windows,
                source=source.model_dump(mode="json"),
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
        if res.created:
            await audit.record(
                c,
                principal.org_id,
                principal.actor,
                Action.INVESTIGATION_STARTED,
                target_type="investigation",
                target_id=res.investigation_id,
                details={"incident_id": str(incident_id), "source": source.model_dump(mode="json")},
            )
        assert res.investigation_id is not None
        row = await get_investigation(c, principal.org_id, res.investigation_id)
    if not res.created:
        response.status_code = 200
    return views.investigation(row)


def resolve_windows(
    state: AppState,
    source: ScenarioSource | LiveSource,
    window: S.WindowIn | None,
    incident_started: datetime,
    now: datetime,
) -> queue.Windows:
    if isinstance(source, ScenarioSource):
        if window is not None:
            raise ApiError(400, "invalid_window", "Scenario sources define their own windows")
        try:
            return state.scenarios.windows(source.scenario)
        except SourceUnavailable as exc:
            raise ApiError(400, "source_unavailable", str(exc)) from None
    # Live collection arrives with the integration registry (roadmap M4).
    if not state.live_collection_available:
        raise ApiError(
            400,
            "source_unavailable",
            "Live telemetry collection is not configured (no integrations registered)",
        )
    if window is None:
        return win.for_incident(incident_started, now).windows
    if window.end <= window.start or window.end - window.start > MAX_WINDOW:
        raise ApiError(
            400, "invalid_window", "The window must be positive and at most 6 hours long"
        )
    if window.end > now + win.LEAD:
        raise ApiError(400, "invalid_window", "The window must not end in the future")
    return queue.Windows(window.start, window.end, window.start - win.BASELINE_LENGTH, window.start)
