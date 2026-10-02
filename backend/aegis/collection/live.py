"""Live snapshot collection for one investigation (roadmap M4).

    integrations (enabled, connector bound to this org, credential decrypts under its binding)
      phase 1: metrics (all services, bounded) + traces (focus services or discovered)  — concurrent
      phase 2: logs for the services phase 1 found (bounded)                             — concurrent
      change feed: the organization's stored change events around the incident window
    → TelemetrySnapshot (+ AnalysisRequest over the investigation's explicit windows)

Each modality gets a collection outcome: ``ok`` / ``partial`` (some sub-queries failed or a
limit was reached) / ``failed`` (with a category, e.g. ``connect``, ``timeout``, ``denied``)
/ ``not_configured`` (no integration). Missing data is never turned into an empty "healthy"
answer: the RCA core reads these outcomes and reports them as coverage gaps.

The change feed is ``ok`` when the organization has received at least one change event in the
last 30 days (a feed exists, even if nothing changed in this window) and ``not_configured``
otherwise — which caps the verdict at PLAUSIBLE and forbids the change-induced mechanism.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from aegis.collection import jaeger, loki, opensearch, prometheus
from aegis.collection.common import AdapterResult, RedactionTally
from aegis.core.model import (
    AnalysisRequest,
    CollectionOutcome,
    CollectionStatus,
    Modality,
    TelemetrySnapshot,
    TimeWindow,
    Tolerances,
)
from aegis.db.engine import tenant_tx
from aegis.db.tables import change_events
from aegis.ingest.changes import to_core
from aegis.integrations.egress import EgressClient
from aegis.integrations.registry import TELEMETRY_KINDS, ConnectorKind, Registry
from aegis.integrations.store import UsableIntegration, usable_integrations
from aegis.jobs.queue import Windows
from aegis.security.crypto import Keyring

FEED_ACTIVITY = timedelta(days=30)
CHANGE_LOOKBACK = timedelta(minutes=30)
MAX_LOG_SERVICES = 30
_MODALITY = {
    ConnectorKind.PROMETHEUS: Modality.METRICS,
    ConnectorKind.JAEGER: Modality.TRACES,
    ConnectorKind.TEMPO: Modality.TRACES,
    ConnectorKind.LOKI: Modality.LOGS,
    ConnectorKind.OPENSEARCH: Modality.LOGS,
    ConnectorKind.KUBERNETES: Modality.CHANGES,
}


class NoTelemetryConfigured(Exception):
    """The organization has no usable telemetry integration."""


@dataclass
class LiveCollector:
    engine: AsyncEngine
    registry: Registry
    keyring: Keyring
    egress: EgressClient
    adapter_timeout_seconds: float = 90.0

    async def has_telemetry(self, conn: AsyncConnection, org_id: UUID) -> bool:
        usable, _ = await usable_integrations(
            conn, org_id, self.registry, self.keyring, TELEMETRY_KINDS
        )
        return bool(usable)

    async def _run(self, integ: UsableIntegration, coro: Any) -> AdapterResult:
        try:
            result: AdapterResult = await asyncio.wait_for(
                coro, timeout=self.adapter_timeout_seconds
            )
            return result
        except TimeoutError:
            modality = _MODALITY.get(integ.connector.kind, Modality.METRICS)
            return AdapterResult(
                modality, integ.connector.id, CollectionStatus.FAILED, "collection timed out"
            )

    async def collect(
        self,
        org_id: UUID,
        windows: Windows,
        focus: list[str],
        tolerances: Tolerances | None = None,
    ) -> tuple[TelemetrySnapshot, AnalysisRequest, dict[str, Any]]:
        tol = tolerances or Tolerances()
        now = datetime.now(UTC)
        async with tenant_tx(self.engine, org_id) as c:
            usable, unusable = await usable_integrations(
                c, org_id, self.registry, self.keyring, TELEMETRY_KINDS
            )
            change_rows, feed_active = await _change_feed(c, org_id, windows, now)
        if not usable:
            raise NoTelemetryConfigured("no usable telemetry integration for this organization")
        tally = RedactionTally()
        trace_windows = [
            ("baseline", windows.baseline_start, windows.baseline_end),
            ("incident", windows.incident_start, windows.incident_end),
        ]

        phase1 = []
        for integ in usable:
            kind = integ.connector.kind
            if kind is ConnectorKind.PROMETHEUS:
                phase1.append(
                    self._run(
                        integ,
                        prometheus.collect(
                            self.egress,
                            integ.connector,
                            integ.credential,
                            start=windows.baseline_start,
                            end=windows.incident_end,
                            step_seconds=tol.bucket_seconds,
                            services=[],
                            collected_at=now,
                        ),
                    )
                )
            elif kind is ConnectorKind.JAEGER:
                phase1.append(
                    self._run(
                        integ,
                        jaeger.collect(
                            self.egress,
                            integ.connector,
                            integ.credential,
                            windows=trace_windows,
                            services=focus,
                            collected_at=now,
                            tally=tally,
                        ),
                    )
                )
        results: list[AdapterResult] = list(await asyncio.gather(*phase1))
        services = set(focus)
        for r in results:
            services |= {m.service for m in r.metrics} | {s.service for s in r.spans}
        log_services = sorted(services)[:MAX_LOG_SERVICES]

        phase2 = []
        for integ in usable:
            kind = integ.connector.kind
            adapter = {ConnectorKind.LOKI: loki, ConnectorKind.OPENSEARCH: opensearch}.get(kind)
            if adapter is not None:
                phase2.append(
                    self._run(
                        integ,
                        adapter.collect(
                            self.egress,
                            integ.connector,
                            integ.credential,
                            windows=trace_windows,
                            services=log_services,
                            collected_at=now,
                            tally=tally,
                        ),
                    )
                )
            elif kind not in (ConnectorKind.PROMETHEUS, ConnectorKind.JAEGER):
                results.append(
                    AdapterResult(
                        _MODALITY.get(kind, Modality.METRICS),
                        integ.connector.id,
                        CollectionStatus.FAILED,
                        f"{kind.value} collection is not implemented in this version",
                    )
                )
        results += list(await asyncio.gather(*phase2))

        outcomes = [
            CollectionOutcome(
                modality=r.modality,
                provider=r.provider,
                status=r.status,
                detail=r.detail[:2048],
                items=r.items,
                truncated=r.truncated,
            )
            for r in results
        ]
        for u in unusable:
            registered = self.registry.get(u.connector_id)
            outcomes.append(
                CollectionOutcome(
                    modality=_MODALITY.get(registered.kind, Modality.METRICS)
                    if registered
                    else Modality.METRICS,
                    provider=u.connector_id,
                    status=CollectionStatus.FAILED,
                    detail=u.reason,
                )
            )
        covered = {o.modality for o in outcomes}
        for modality in (Modality.METRICS, Modality.LOGS, Modality.TRACES):
            if modality not in covered:
                outcomes.append(
                    CollectionOutcome(
                        modality=modality, provider="none", status=CollectionStatus.NOT_CONFIGURED
                    )
                )
        changes = [to_core(dict(r), now) for r in change_rows]
        outcomes.append(
            CollectionOutcome(
                modality=Modality.CHANGES,
                provider="aegis-change-feed",
                status=CollectionStatus.OK if feed_active else CollectionStatus.NOT_CONFIGURED,
                detail="" if feed_active else "no change events received in the last 30 days",
                items=len(changes),
            )
        )
        snapshot = TelemetrySnapshot(
            captured_at=now,
            collected_window=TimeWindow(start=windows.baseline_start, end=windows.incident_end),
            synthetic=False,
            description="live collection",
            metrics=[m for r in results for m in r.metrics],
            logs=[lg for r in results for lg in r.logs],
            spans=[s for r in results for s in r.spans],
            changes=changes,
            collection=outcomes,
        )
        request = AnalysisRequest(
            incident=TimeWindow(start=windows.incident_start, end=windows.incident_end),
            baselines=[TimeWindow(start=windows.baseline_start, end=windows.baseline_end)],
            focus_services=focus[:20],
            tolerances=tol,
        )
        redaction = {"applied": True, "at": "collection", "counts": dict(Counter(tally.counts))}
        return snapshot, request, redaction


async def _change_feed(
    conn: AsyncConnection, org_id: UUID, windows: Windows, now: datetime
) -> tuple[list[Any], bool]:
    active = (
        await conn.execute(
            select(func.count())
            .select_from(change_events)
            .where(
                change_events.c.org_id == org_id, change_events.c.received_at >= now - FEED_ACTIVITY
            )
        )
    ).scalar_one() > 0
    rows = (
        (
            await conn.execute(
                select(change_events)
                .where(
                    change_events.c.org_id == org_id,
                    change_events.c.ts >= windows.baseline_start - CHANGE_LOOKBACK,
                    change_events.c.ts < windows.incident_end,
                )
                .order_by(change_events.c.ts)
                .limit(5000)
            )
        )
        .mappings()
        .all()
    )
    return list(rows), active
