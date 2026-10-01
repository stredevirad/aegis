"""Change events: deployments, rollouts, configuration, feature flags, infrastructure (ADR-013).

Change events are evidence for change-induced hypotheses, never proof of causality: the RCA
core still requires propagation/verification before it calls a change the root cause.

Idempotency: ``(org, source, external_id)`` is unique. When the sender supplies no
``external_id``, one is derived from the event's content (kind, service, environment,
version, timestamp, summary), so re-sending the same event does not duplicate it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from aegis.core.model import ChangeEvent, ChangeKind, Provenance
from aegis.core.sanitize import clean_name, clean_text
from aegis.db.tables import change_events

MAX_SCOPE_ITEMS = 32
MAX_METADATA_BYTES = 4096
FUTURE_SKEW = timedelta(minutes=5)

ChangeKindIn = Literal["deployment", "config", "feature_flag", "rollout", "infrastructure", "other"]

_CORE_KIND = {
    "deployment": ChangeKind.DEPLOYMENT,
    "config": ChangeKind.CONFIG,
    "feature_flag": ChangeKind.FEATURE_FLAG,
    "rollout": ChangeKind.ROLLOUT,
    "infrastructure": ChangeKind.OTHER,
    "other": ChangeKind.OTHER,
}


class ChangeEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[str, Field(min_length=1, max_length=100)]
    kind: ChangeKindIn
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    ts: AwareDatetime
    service: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    environment: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    version: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    actor: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    external_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    scope: Annotated[dict[str, str], Field(max_length=MAX_SCOPE_ITEMS)] = Field(
        default_factory=dict
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scope")
    @classmethod
    def _scope_bounds(cls, v: dict[str, str]) -> dict[str, str]:
        for k, val in v.items():
            if len(k) > 100 or len(val) > 256:
                raise ValueError("scope keys are limited to 100 and values to 256 characters")
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata_bounds(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v, default=str).encode()) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata is limited to {MAX_METADATA_BYTES} bytes")
        return v


def _derived_id(e: ChangeEventIn) -> str:
    basis = "\x1f".join(
        [e.kind, e.service or "", e.environment or "", e.version or "", e.ts.isoformat(), e.summary]
    )
    return "sha256:" + hashlib.sha256(basis.encode()).hexdigest()[:40]


async def store(
    conn: AsyncConnection, org_id: UUID, event: ChangeEventIn, via_key: UUID | None
) -> tuple[UUID, bool]:
    """Insert unless already present; returns (id, created)."""
    ts = min(event.ts, datetime.now(UTC) + FUTURE_SKEW)
    external_id = clean_name(event.external_id) if event.external_id else _derived_id(event)
    row = (
        await conn.execute(
            pg_insert(change_events)
            .values(
                org_id=org_id,
                source=clean_name(event.source),
                external_id=external_id,
                kind=event.kind,
                service=clean_name(event.service) if event.service else None,
                environment=clean_name(event.environment) if event.environment else None,
                version=clean_name(event.version) if event.version else None,
                summary=clean_text(event.summary),
                actor=clean_name(event.actor) if event.actor else None,
                ts=ts,
                scope={clean_name(k): clean_name(v) for k, v in event.scope.items()},
                metadata=json.loads(json.dumps(event.metadata, default=str)),
                received_via=via_key,
            )
            .on_conflict_do_nothing(index_elements=["org_id", "source", "external_id"])
            .returning(change_events.c.id)
        )
    ).first()
    if row is not None:
        return row[0], True
    existing: UUID = (
        await conn.execute(
            select(change_events.c.id).where(
                change_events.c.org_id == org_id,
                change_events.c.source == clean_name(event.source),
                change_events.c.external_id == external_id,
            )
        )
    ).scalar_one()
    return existing, False


def to_core(row: dict[str, Any], collected_at: datetime) -> ChangeEvent:
    """A stored change event as RCA core input (used by the live collector)."""
    scope = dict(row.get("scope") or {})
    if row["kind"] == "infrastructure":
        scope.setdefault("change_type", "infrastructure")
    if row.get("version"):
        scope.setdefault("version", row["version"])
    return ChangeEvent(
        source_id=str(row["id"]),
        ts=row["ts"],
        service=row.get("service"),
        kind=_CORE_KIND[row["kind"]],
        summary=row["summary"],
        environment=row.get("environment"),
        scope={k: v for k, v in list(scope.items())[:32]},
        provenance=Provenance(
            provider=f"change-feed:{row['source']}"[:200],
            query="aegis change_events",
            collected_at=collected_at,
        ),
    )
