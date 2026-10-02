"""Runs due post-action verifications (worker maintenance loop)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from aegis import audit
from aegis.audit import Action, Actor
from aegis.collection.live import LiveCollector, NoTelemetryConfigured
from aegis.db.engine import system_tx, tenant_tx
from aegis.db.tables import investigations, remediation_actions, remediation_proposals
from aegis.jobs.queue import Windows
from aegis.remediation import verify
from aegis.remediation.flags import FlagError
from aegis.remediation.service import Remediation, RemediationError

log = logging.getLogger("aegis.remediation")
LEASE_SECONDS = 300


@dataclass
class Verifier:
    engine: AsyncEngine
    remediation: Remediation
    collector: LiveCollector | None

    async def run_due(self, worker: str, limit: int = 5) -> int:
        done = 0
        for _ in range(limit):
            async with system_tx(self.engine) as c:
                row = (
                    await c.execute(
                        text("SELECT * FROM aegis.claim_due_verification(:w, :l)"),
                        {"w": worker, "l": LEASE_SECONDS},
                    )
                ).first()
            if row is None:
                break
            try:
                await self.verify_one(row.org_id, row.proposal_id, worker)
            except Exception:
                log.exception("verification failed", extra={"proposal_id": str(row.proposal_id)})
            done += 1
        return done

    async def verify_one(self, org_id: UUID, proposal_id: UUID, worker: str) -> dict[str, Any]:
        async with tenant_tx(self.engine, org_id) as c:
            p = (
                (
                    await c.execute(
                        select(remediation_proposals).where(
                            remediation_proposals.c.org_id == org_id,
                            remediation_proposals.c.id == proposal_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
            action = (
                (
                    await c.execute(
                        select(remediation_actions).where(
                            remediation_actions.c.id == p["action_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            inv = None
            if p["investigation_id"]:
                inv = (
                    (
                        await c.execute(
                            select(investigations).where(
                                investigations.c.id == p["investigation_id"]
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
        executed_at = p["executed_at"] or datetime.now(UTC)
        during, after = verify.windows_for(
            executed_at,
            p["verify_after"] or executed_at,
            (inv["window_start"], inv["window_end"]) if inv else None,
        )
        result: dict[str, Any] = {
            "during_window": [during[0].isoformat(), during[1].isoformat()],
            "after_window": [after[0].isoformat(), after[1].isoformat()],
        }
        synthetic = bool(inv and (inv["source"] or {}).get("kind") == "scenario")
        if synthetic:
            result.update(
                result="not_evaluable", reason="the diagnosis used synthetic scenario data"
            )
        elif self.collector is None:
            result.update(result="not_evaluable", reason="live collection is not configured")
        else:
            try:
                snapshot, _, _ = await self.collector.collect(
                    org_id,
                    Windows(after[0], after[1], during[0], during[1]),
                    [action["service"]] if action["service"] else [],
                )
                service = action["service"] or ""
                d, a = (
                    verify.window_stats(snapshot, service, *during),
                    verify.window_stats(snapshot, service, *after),
                )
                outcome, reason = verify.decide(d, a)
                result.update(
                    result=outcome,
                    reason=reason,
                    during=d.as_dict(),
                    after=a.as_dict(),
                    collection=[o.model_dump(mode="json") for o in snapshot.collection],
                )
            except NoTelemetryConfigured:
                result.update(result="not_evaluable", reason="no usable telemetry integration")
        try:
            async with tenant_tx(self.engine, org_id) as c:
                client = await self.remediation._flag_client(c, org_id, action["integration_id"])
            state = await client.get(p["params"]["flag_key"])
            expected = (p["execution"] or {}).get("state_after") or {}
            result["target_state"] = state.as_dict()
            result["target_unchanged_since_execution"] = state.as_dict() == expected
        except (FlagError, RemediationError) as exc:
            result["target_state_error"] = str(exc)[:200]
        status = "verified" if result["result"] == "recovered" else "verification_failed"
        async with tenant_tx(self.engine, org_id) as c:
            updated = (
                await c.execute(
                    update(remediation_proposals)
                    .where(
                        remediation_proposals.c.org_id == org_id,
                        remediation_proposals.c.id == proposal_id,
                        remediation_proposals.c.status == "verifying",
                        remediation_proposals.c.verify_lease_owner == worker,
                    )
                    .values(
                        status=status,
                        verification={**result, "at": datetime.now(UTC).isoformat()},
                        verify_lease_until=None,
                        updated_at=func.now(),
                    )
                    .returning(remediation_proposals.c.id)
                )
            ).first()
            if updated is not None:
                await audit.record(
                    c,
                    org_id,
                    Actor.system(worker),
                    Action.REMEDIATION_VERIFIED,
                    target_type="remediation",
                    target_id=proposal_id,
                    details={"result": result["result"], "reason": result.get("reason")},
                )
        return result
