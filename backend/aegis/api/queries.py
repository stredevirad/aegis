"""Shared tenant-scoped read queries used by several routers.

Every query takes the caller's ``org_id`` and filters on it explicitly in addition to RLS
(ADR-012: a forgotten predicate fails closed rather than leaking, and a forgotten RLS policy
is caught by the explicit predicate).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncConnection

from aegis.db.tables import incidents, investigation_jobs, investigations


def investigation_select() -> Select[Any]:
    return select(
        investigations,
        investigation_jobs.c.status.label("job_status"),
        investigation_jobs.c.attempts.label("job_attempts"),
    ).select_from(
        investigations.outerjoin(
            investigation_jobs,
            (investigation_jobs.c.investigation_id == investigations.c.id)
            & (investigation_jobs.c.org_id == investigations.c.org_id),
        )
    )


async def get_investigation(conn: AsyncConnection, org_id: UUID, inv_id: UUID) -> Any | None:
    return (
        (
            await conn.execute(
                investigation_select().where(
                    investigations.c.org_id == org_id, investigations.c.id == inv_id
                )
            )
        )
        .mappings()
        .first()
    )


async def get_incident(conn: AsyncConnection, org_id: UUID, incident_id: UUID) -> Any | None:
    return (
        (
            await conn.execute(
                select(incidents).where(incidents.c.org_id == org_id, incidents.c.id == incident_id)
            )
        )
        .mappings()
        .first()
    )
