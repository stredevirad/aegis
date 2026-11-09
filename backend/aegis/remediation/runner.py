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


STUCK_AFTER_SECONDS = 300


async def reconcile_stuck(
    engine: AsyncEngine,
    remediation: Remediation,
    worker: str,
    stale_seconds: int = STUCK_AFTER_SECONDS,
    limit: int = 5,
) -> int:
    """Rows left in 'executing'/'rolling_back' by a crash: re-read the target, record what is
    observed, mark failed with ``uncertain: true``. Never re-executes (no blind retry).

    Each row is isolated: an unexpected error is logged and the sweep moves on. The row keeps
    its lease, so a later sweep retries it after ``LEASE_SECONDS``."""
    handled = 0
    for _ in range(limit):
        async with system_tx(engine) as c:
            row = (
                await c.execute(
                    text("SELECT * FROM aegis.claim_stuck_remediation(:w, :s, :l)"),
                    {"w": worker, "s": stale_seconds, "l": LEASE_SECONDS},
                )
            ).first()
        if row is None:
            break
        handled += 1
        try:
            await _reconcile_one(
                engine, remediation, worker, row.org_id, row.proposal_id, row.status
            )
        except Exception:
            # Exception, not BaseException: cancellation must still stop the worker.
            log.exception(
                "reconciling a stuck remediation failed",
                extra={"proposal_id": str(row.proposal_id)},
            )
    return handled


async def _reconcile_one(
    engine: AsyncEngine,
    remediation: Remediation,
    worker: str,
    org_id: UUID,
    proposal_id: UUID,
    status: str,
) -> None:
    observed: dict[str, Any] = {}
    async with tenant_tx(engine, org_id) as c:
        p = (
            (
                await c.execute(
                    select(remediation_proposals).where(remediation_proposals.c.id == proposal_id)
                )
            )
            .mappings()
            .first()
        )
        action = (
            None
            if p is None
            else (
                await c.execute(
                    select(remediation_actions).where(remediation_actions.c.id == p["action_id"])
                )
            )
            .mappings()
            .first()
        )
    if p is None or action is None:
        # deleted since it was claimed (its incident or organization was removed)
        log.warning(
            "stuck remediation vanished before it was reconciled",
            extra={"proposal_id": str(proposal_id)},
        )
        return
    try:
        async with tenant_tx(engine, org_id) as c:
            client = await remediation._flag_client(c, org_id, action["integration_id"])
        observed = (await client.get(p["params"]["flag_key"])).as_dict()
    except (FlagError, RemediationError) as exc:
        observed = {"error": str(exc)[:200]}
    final = "failed" if status == "executing" else "rollback_failed"
    record = {
        "ok": False,
        "uncertain": True,
        "reason": f"interrupted while {status}; reconciled by {worker}",
        "observed": observed,
        "at": datetime.now(UTC).isoformat(),
    }
    column = "execution" if status == "executing" else "rollback"
    async with tenant_tx(engine, org_id) as c:
        done = (
            await c.execute(
                update(remediation_proposals)
                .where(
                    remediation_proposals.c.org_id == org_id,
                    remediation_proposals.c.id == proposal_id,
                    remediation_proposals.c.status == status,
                    remediation_proposals.c.verify_lease_owner == worker,
                )
                .values(
                    {
                        "status": final,
                        column: {**(p[column] or {}), **record},
                        "verify_lease_until": None,
                        "updated_at": func.now(),
                    }
                )
                .returning(remediation_proposals.c.id)
            )
        ).first()
        if done is not None:
            await audit.record(
                c,
                org_id,
                Actor.system(worker),
                Action.REMEDIATION_EXECUTED
                if status == "executing"
                else Action.REMEDIATION_ROLLED_BACK,
                target_type="remediation",
                target_id=proposal_id,
                details={"phase": "reconciled", "status": final, "observed": observed},
            )
