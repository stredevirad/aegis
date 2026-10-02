"""Alert ingestion: dedup, grouping into incidents, lifecycle, automatic investigation.

One payload is one transaction, whatever the number of alerts (bounded by
``MAX_ALERTS_PER_PAYLOAD``): duplicates do not create transactions of their own.

* **Dedup.** An alert *occurrence* is ``(org, fingerprint, reported start)``. Re-deliveries
  (Alertmanager re-sends on every group/repeat interval) update that row: status, end time,
  annotations, ``last_received_at`` and ``receive_count``.
* **Grouping.** Alerts of the same (service, environment) attach to the incident that is
  open for grouping for that key, as long as it received an alert within the organization's
  grouping window; otherwise grouping on the old incident closes and a new incident opens.
  Two concurrent webhooks for the same group produce one incident (see Concurrency).
* **Lifecycle.** When every alert of an incident is resolved and ``auto_resolve`` is on, the
  incident becomes ``resolved`` and grouping closes. A resolved alert with no recorded firing
  occurrence is counted and ignored (no incident is created for it).
* **Automatic investigation.** A newly created incident is investigated once
  (``auto_investigated_at``) when ``auto_investigate`` is on and a default source exists;
  queue limits defer it (recorded, never dropped).

**Concurrency.** The first statement of every ingestion transaction takes one per-organization
advisory lock, so ingestion for an organization is serialised and no other lock order is
possible between two payloads (per-group locks were dropped: a payload touching groups
{G1, G2} and one touching {G2} could deadlock against the audit chain lock). The partial
unique index on open incident groups remains as a backstop. Audit records are buffered and
written last (the audit lock-order rule, ``aegis.audit.log``). Re-deliveries that change
nothing new (pure duplicates) write no audit record, so Alertmanager's periodic resends do
not grow the hash chain.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from aegis.audit import Action, Actor, AuditBuffer
from aegis.db.tables import alerts, incidents
from aegis.ingest.alerts import SEVERITY_RANK, NormalizedAlert
from aegis.investigations import windows as win
from aegis.investigations.sources import ScenarioRegistry, SourceUnavailable
from aegis.jobs import queue
from aegis.org_settings import OrgSettings


@dataclass
class IngestResult:
    received: int = 0
    new_occurrences: int = 0
    duplicates: int = 0
    ignored_resolved: int = 0
    clock_skew_clamped: int = 0
    incidents_created: list[UUID] = field(default_factory=list)
    incidents_updated: list[UUID] = field(default_factory=list)
    incidents_resolved: list[UUID] = field(default_factory=list)
    investigations: dict[str, str] = field(default_factory=dict)  # incident -> enqueue state

    def as_dict(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "new_occurrences": self.new_occurrences,
            "duplicates": self.duplicates,
            "ignored_resolved": self.ignored_resolved,
            "clock_skew_clamped": self.clock_skew_clamped,
            "incidents_created": [str(i) for i in self.incidents_created],
            "incidents_updated": [str(i) for i in self.incidents_updated],
            "incidents_resolved": [str(i) for i in self.incidents_resolved],
            "investigations": self.investigations,
        }


def _max_severity(values: list[str]) -> str:
    return max(values, key=lambda s: SEVERITY_RANK.get(s, 2))


async def _open_incident(
    conn: AsyncConnection, org_id: UUID, group_key: str, now: datetime, window: timedelta
) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                select(incidents)
                .where(incidents.c.org_id == org_id, incidents.c.group_key == group_key)
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    last = row["last_alert_at"] or row["created_at"]
    if last < now - window:
        await conn.execute(
            update(incidents)
            .where(incidents.c.id == row["id"])
            .values(group_key=None, updated_at=func.now())
        )
        return None
    return dict(row)


OccurrenceKey = tuple[str, datetime | None]


def occurrence_key(a: NormalizedAlert) -> OccurrenceKey:
    return (a.fingerprint, a.reported_start)


async def _existing_occurrences(
    conn: AsyncConnection, org_id: UUID, batch: list[NormalizedAlert]
) -> dict[OccurrenceKey, tuple[UUID, datetime]]:
    """Known occurrences for the batch: key -> (incident, stored starts_at).

    With a reported start, the occurrence is (fingerprint, start). Without one (generic
    senders that do not report start times), a delivery refers to the currently firing
    occurrence of that fingerprint, if any.
    """
    found: dict[OccurrenceKey, tuple[UUID, datetime]] = {}
    reported = [a for a in batch if a.reported_start is not None]
    for i in range(0, len(reported), 100):
        chunk = reported[i : i + 100]
        rows = await conn.execute(
            select(alerts.c.fingerprint, alerts.c.starts_at, alerts.c.incident_id).where(
                alerts.c.org_id == org_id,
                or_(
                    *(
                        and_(
                            alerts.c.fingerprint == a.fingerprint,
                            alerts.c.starts_at == a.reported_start,
                        )
                        for a in chunk
                    )
                ),
            )
        )
        for r in rows:
            found[(r.fingerprint, r.starts_at)] = (r.incident_id, r.starts_at)
    unreported = sorted({a.fingerprint for a in batch if a.reported_start is None})
    if unreported:
        rows = await conn.execute(
            select(alerts.c.fingerprint, alerts.c.starts_at, alerts.c.incident_id)
            .where(
                alerts.c.org_id == org_id,
                alerts.c.fingerprint.in_(unreported),
                alerts.c.status == "firing",
            )
            .order_by(alerts.c.fingerprint, alerts.c.starts_at.desc())
            .ext(distinct_on(alerts.c.fingerprint))
        )
        for r in rows:
            found[(r.fingerprint, None)] = (r.incident_id, r.starts_at)
    return found


async def _upsert(
    conn: AsyncConnection,
    org_id: UUID,
    incident_id: UUID,
    a: NormalizedAlert,
    via: UUID | None,
    starts_at: datetime,
) -> bool:
    """Insert or refresh one occurrence; returns True if it was new."""
    ins = pg_insert(alerts).values(
        org_id=org_id,
        incident_id=incident_id,
        source=a.source,
        fingerprint=a.fingerprint,
        status=a.status,
        name=a.name,
        severity=a.severity,
        service=a.service,
        environment=a.environment,
        labels=a.labels,
        annotations=a.annotations,
        generator_url=a.generator_url,
        starts_at=starts_at,
        ends_at=a.ends_at,
        received_via=via,
    )
    stmt = ins.on_conflict_do_update(
        index_elements=["org_id", "fingerprint", "starts_at"],
        set_={
            "status": ins.excluded.status,
            "ends_at": ins.excluded.ends_at,
            "annotations": ins.excluded.annotations,
            "last_received_at": func.now(),
            "receive_count": alerts.c.receive_count + 1,
        },
    ).returning(text("(xmax = 0) AS inserted"))
    return bool((await conn.execute(stmt)).scalar_one())


async def ingest_alerts(
    conn: AsyncConnection,
    *,
    org_id: UUID,
    batch: list[NormalizedAlert],
    settings: OrgSettings,
    actor: Actor,
    via_key: UUID | None,
    scenarios: ScenarioRegistry,
    queue_limit: int,
    max_attempts: int,
    now: datetime | None = None,
) -> IngestResult:
    now = now or datetime.now(UTC)
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"aegis-ingest:{org_id}"},
    )
    pending = AuditBuffer()
    result = IngestResult(received=len(batch))
    result.clock_skew_clamped = sum(1 for a in batch if a.clock_skew_clamped)
    known = await _existing_occurrences(conn, org_id, batch)

    groups: dict[str, list[NormalizedAlert]] = defaultdict(list)
    refreshed: dict[UUID, bool] = {}  # incident -> any firing re-delivery
    for a in batch:
        if occurrence_key(a) in known:
            # re-delivery of a known occurrence: refresh it where it already lives
            incident_id, stored_start = known[occurrence_key(a)]
            await _upsert(conn, org_id, incident_id, a, via_key, stored_start)
            result.duplicates += 1
            refreshed[incident_id] = refreshed.get(incident_id, False) or a.status == "firing"
        elif a.status == "resolved":
            result.ignored_resolved += 1
        else:
            groups[a.group_key].append(a)
    for incident_id in sorted(refreshed):
        if refreshed[incident_id]:  # a still-firing alert keeps the grouping window open
            await conn.execute(
                update(incidents)
                .where(incidents.c.org_id == org_id, incidents.c.id == incident_id)
                .values(last_alert_at=now, updated_at=func.now())
            )
        else:
            await _maybe_resolve(conn, org_id, incident_id, settings, pending, result)

    window = timedelta(minutes=settings.grouping_window_minutes)
    for group_key in sorted(groups):
        members = groups[group_key]
        incident = await _open_incident(conn, org_id, group_key, now, window)
        first = min(members, key=lambda m: m.effective_start)
        severity = _max_severity([m.severity for m in members])
        if incident is None:
            title = first.annotations.get("summary") or first.name
            if first.service:
                title = f"{first.name} on {first.service}" if title == first.name else title
            incident_id = (
                await conn.execute(
                    pg_insert(incidents)
                    .values(
                        org_id=org_id,
                        title=title[:300],
                        description=(first.annotations.get("description") or "")[:4000],
                        severity=severity,
                        service=first.service,
                        environment=first.environment,
                        source=first.source,
                        group_key=group_key,
                        labels=first.labels,
                        annotations=first.annotations,
                        started_at=first.effective_start,
                        created_by=actor.id,
                        last_alert_at=now,
                    )
                    .returning(incidents.c.id)
                )
            ).scalar_one()
            result.incidents_created.append(incident_id)
            pending.add(
                Action.INCIDENT_CREATED,
                target_type="incident",
                target_id=incident_id,
                details={"source": first.source, "severity": severity, "group_key": group_key},
            )
        else:
            incident_id = incident["id"]
            if SEVERITY_RANK[severity] > SEVERITY_RANK.get(incident["severity"], 2):
                await conn.execute(
                    update(incidents).where(incidents.c.id == incident_id).values(severity=severity)
                )
            result.incidents_updated.append(incident_id)
        new = 0
        for a in members:
            start = a.reported_start or a.effective_start
            if await _upsert(conn, org_id, incident_id, a, via_key, start):
                new += 1
            else:
                result.duplicates += 1
        result.new_occurrences += new
        await conn.execute(
            update(incidents)
            .where(incidents.c.id == incident_id)
            .values(
                last_alert_at=now,
                alert_count=incidents.c.alert_count + new,
                updated_at=func.now(),
            )
        )
        if incident is None:
            await _auto_investigate(
                conn,
                org_id,
                incident_id,
                first.effective_start,
                settings,
                actor,
                pending,
                scenarios,
                queue_limit,
                max_attempts,
                now,
                result,
            )

    if result.new_occurrences or result.incidents_created or result.incidents_resolved:
        pending.add(
            Action.ALERTS_INGESTED,
            target_type="alert_batch",
            details={k: v for k, v in result.as_dict().items() if k != "investigations"},
        )
    await pending.flush(conn, org_id, actor)  # audit last: see the lock-order rule
    return result


async def _maybe_resolve(
    conn: AsyncConnection,
    org_id: UUID,
    incident_id: UUID,
    settings: OrgSettings,
    pending: AuditBuffer,
    result: IngestResult,
) -> None:
    if not settings.auto_resolve:
        return
    firing = (
        await conn.execute(
            select(func.count())
            .select_from(alerts)
            .where(
                alerts.c.org_id == org_id,
                alerts.c.incident_id == incident_id,
                alerts.c.status == "firing",
            )
        )
    ).scalar_one()
    if firing:
        return
    resolved = (
        await conn.execute(
            update(incidents)
            .where(
                incidents.c.org_id == org_id,
                incidents.c.id == incident_id,
                incidents.c.status.in_(("open", "investigating")),
            )
            .values(
                status="resolved", resolved_at=func.now(), group_key=None, updated_at=func.now()
            )
            .returning(incidents.c.id)
        )
    ).first()
    if resolved is not None:
        result.incidents_resolved.append(incident_id)
        pending.add(
            Action.INCIDENT_AUTO_RESOLVED,
            target_type="incident",
            target_id=incident_id,
            details={"reason": "all alerts resolved"},
        )


async def _auto_investigate(
    conn: AsyncConnection,
    org_id: UUID,
    incident_id: UUID,
    started_at: datetime,
    settings: OrgSettings,
    actor: Actor,
    pending: AuditBuffer,
    scenarios: ScenarioRegistry,
    queue_limit: int,
    max_attempts: int,
    now: datetime,
    result: IngestResult,
) -> None:
    if not settings.auto_investigate or settings.default_source is None:
        return
    source = settings.default_source
    try:
        if source.kind == "scenario":
            w = scenarios.windows(source.scenario)
        else:
            w = win.for_incident(started_at, now).windows
    except SourceUnavailable:
        result.investigations[str(incident_id)] = "source_unavailable"
        return
    res = await queue.enqueue_investigation(
        conn,
        org_id=org_id,
        incident_id=incident_id,
        windows=w,
        source=source.model_dump(mode="json"),
        trigger="auto",
        requested_by=actor.id,
        idempotency_key=None,
        queue_limit=queue_limit,
        max_attempts=max_attempts,
    )
    result.investigations[str(incident_id)] = res.state
    await conn.execute(
        update(incidents)
        .where(incidents.c.id == incident_id)
        .values(auto_investigated_at=func.now())
    )
    action = {
        "queued": Action.INVESTIGATION_STARTED,
        "deferred": Action.INVESTIGATION_DEFERRED,
        "skipped": Action.INVESTIGATION_SKIPPED,
        "existing": Action.INVESTIGATION_STARTED,
    }[res.state]
    pending.add(
        action,
        target_type="incident",
        target_id=incident_id,
        details={
            "trigger": "auto",
            "investigation_id": str(res.investigation_id) if res.investigation_id else None,
        },
    )
