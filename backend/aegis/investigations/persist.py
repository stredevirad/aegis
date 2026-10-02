"""Persisting snapshots and analyses (tenant transaction supplied by the caller).

Evidence is keyed by ``(org_id, investigation_id, evidence_id)`` and inserted, never
upserted, so evidence can never be re-homed to another investigation or tenant (audit
finding DATA-01 / S-C1). ``store_analysis`` replaces the rows of *this* investigation only
(a retried attempt after a crash), inside the caller's fenced result transaction.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from aegis.core.model import TelemetrySnapshot
from aegis.core.result import Analysis
from aegis.db.tables import (
    candidates,
    evidence,
    evidence_relations,
    investigations,
    root_causes,
    snapshots,
)

_CHUNK = 500


async def store_snapshot(
    conn: AsyncConnection,
    org_id: UUID,
    investigation_id: UUID,
    snapshot: TelemetrySnapshot,
    digest: str,
    redaction: dict[str, Any],
) -> bool:
    """Insert the snapshot unless one exists (a retried attempt reuses the stored one)."""
    result = await conn.execute(
        pg_insert(snapshots)
        .values(
            org_id=org_id,
            investigation_id=investigation_id,
            schema_version=snapshot.schema_version,
            digest=digest,
            synthetic=snapshot.synthetic,
            payload=snapshot.model_dump(mode="json"),
            redaction=redaction,
        )
        .on_conflict_do_nothing(index_elements=["org_id", "investigation_id"])
        .returning(snapshots.c.investigation_id)
    )
    return result.first() is not None


async def load_snapshot(
    conn: AsyncConnection, org_id: UUID, investigation_id: UUID
) -> TelemetrySnapshot | None:
    payload = (
        await conn.execute(
            select(snapshots.c.payload).where(
                snapshots.c.org_id == org_id, snapshots.c.investigation_id == investigation_id
            )
        )
    ).scalar_one_or_none()
    return None if payload is None else TelemetrySnapshot.model_validate(payload)


async def _insert_many(conn: AsyncConnection, table: Any, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), _CHUNK):
        await conn.execute(insert(table), rows[i : i + _CHUNK])


async def store_analysis(
    conn: AsyncConnection,
    org_id: UUID,
    investigation_id: UUID,
    analysis: Analysis,
    status: str = "completed",
) -> None:
    for table in (evidence_relations, evidence, candidates, root_causes):
        await conn.execute(
            delete(table).where(
                table.c.org_id == org_id, table.c.investigation_id == investigation_id
            )
        )
    key = {"org_id": org_id, "investigation_id": investigation_id}
    await _insert_many(
        conn,
        evidence,
        [
            {
                **key,
                "evidence_id": e.id,
                "kind": e.kind.value,
                "modality": e.modality.value if e.modality else None,
                "service": e.service,
                "observed_at": e.observed_at,
                "summary": e.summary,
                "attributes": e.model_dump(mode="json")["attributes"],
                "sources": e.sources,
            }
            for e in analysis.evidence
        ],
    )
    relations = {(r.source, r.target, r.kind.value) for r in analysis.relations}
    await _insert_many(
        conn,
        evidence_relations,
        [{**key, "source": s, "target": t, "kind": k} for s, t, k in sorted(relations)],
    )
    await _insert_many(
        conn,
        candidates,
        [
            {
                **key,
                "service": c.service,
                "rank": c.rank,
                "rule": c.rule,
                "rule_sentence": c.rule_sentence,
                "hypothesis": c.hypothesis.value,
                "inferred": c.inferred,
                "features": c.model_dump(mode="json")["features"],
                "checks": [ch.model_dump(mode="json") for ch in c.checks],
                "supporting": c.supporting_evidence_ids,
                "contradicting": c.contradicting_evidence_ids,
            }
            for c in analysis.candidates
        ],
    )
    await _insert_many(
        conn,
        root_causes,
        [
            {
                **key,
                "service": rc.service,
                "mechanism": rc.mechanism.value,
                "support": rc.support.value,
                "supporting": rc.supporting_evidence_ids,
                "contradicting": rc.contradicting_evidence_ids,
            }
            for rc in analysis.verdict.root_causes
        ],
    )
    v = analysis.verdict
    await conn.execute(
        update(investigations)
        .where(investigations.c.org_id == org_id, investigations.c.id == investigation_id)
        .values(
            status=status,
            classification=v.classification.value,
            support=v.support.value,
            display_label=v.display_label,
            summary=v.summary,
            verdict={
                **v.model_dump(mode="json"),
                "decision_trace": analysis.decision_trace,
                "snapshot_digest": analysis.snapshot_digest,
                "analysis_schema_version": analysis.schema_version,
            },
            error=None,
            finished_at=func.now() if status == "completed" else None,
        )
    )


async def complete(
    conn: AsyncConnection,
    org_id: UUID,
    investigation_id: UUID,
    ai: dict[str, Any],
    verdict: dict[str, Any] | None,
) -> None:
    """Mark completed and attach the AI assessment (or why there is none) to the verdict.
    The caller holds the investigation row lock and passes the stored verdict."""
    await conn.execute(
        update(investigations)
        .where(investigations.c.org_id == org_id, investigations.c.id == investigation_id)
        .values(
            status="completed",
            finished_at=func.now(),
            verdict={**(verdict or {}), "ai": json.loads(json.dumps(ai, default=str))},
        )
    )
