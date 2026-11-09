"""Remediation lifecycle (ADR-014 + Council C amendment).

    proposed ──approve──▶ approved ──execute──▶ executing ──▶ verifying ──▶ verified
        │                    │                     │               └──────▶ verification_failed
        └──reject──▶ rejected └──(expiry)──▶ expired └──▶ failed
    executed/verifying/verified/verification_failed ──rollback──▶ rolling_back ──▶ rolled_back | rollback_failed

Invariants (each tested in ``tests/api/test_remediation.py``):

* **Parameters come from the catalog**: a proposal names a catalog action; the flag key and
  target value are copied from it. Requests and models cannot supply parameters.
* **Prior state** is read from the target when proposing; approval re-reads it and refuses if
  it changed (stale proposal).
* **Approval binds** ``sha256(proposal id, params hash, prior state)`` and expires
  (``remediation_approval_minutes``, default 15). The approver must not be the proposer
  (two-person rule) unless the organization explicitly allows self-approval.
* **Execution is compare-and-set**: ``UPDATE … SET status='executing' WHERE status='approved'
  AND approval_hash = <recomputed from the row> AND approval_expires_at > now()``. A replayed,
  expired or tampered approval matches nothing. The executor then re-reads the target and
  aborts if it differs from the approved prior state, and writes with the flag service's own
  compare-and-set (``if_version``). **No automatic retry**: an uncertain outcome is recorded as
  failed and must be inspected.
* **Execution is off by default** per organization (``remediation_enabled``).
* **Rollback** applies the inverse that was approved together with the forward action (so it
  does not wait for a second approval mid-incident), again compare-and-set against the state
  the execution produced.
* **The final writes are compare-and-set too** (``WHERE status = 'executing'`` /
  ``'rolling_back'``). The stuck-row sweeper (``runner.reconcile_stuck``) cannot tell a slow
  request from a crash and may mark the row failed first; the late result then matches no row,
  the reconciled record stays, and the late outcome is kept in the audit log (no lost update).
* The literal diff (flag key, current value, new value, rollback value) is what reviewers see.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from aegis import audit
from aegis.audit import Action, Actor
from aegis.config import Settings
from aegis.db.engine import tenant_tx
from aegis.db.tables import (
    evidence,
    integrations,
    organizations,
    remediation_actions,
    remediation_proposals,
)
from aegis.integrations.egress import EgressClient
from aegis.integrations.registry import ConnectorKind, Registry
from aegis.integrations.store import usable_integrations
from aegis.org_settings import read_settings
from aegis.remediation.flags import FlagClient, FlagError, FlagState
from aegis.security.crypto import Keyring

OPEN = ("proposed", "approved", "executing", "verifying", "rolling_back")
ROLLBACKABLE = ("executed", "verifying", "verified", "verification_failed")


class RemediationError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def params_hash(
    action_id: UUID, integration_id: UUID, params: dict[str, Any], inverse: dict[str, Any]
) -> str:
    basis = {
        "action": str(action_id),
        "integration": str(integration_id),
        "params": params,
        "inverse": inverse,
    }
    return hashlib.sha256(canonical(basis).encode()).hexdigest()


def approval_hash(proposal_id: UUID, p_hash: str, prior: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical({"proposal": str(proposal_id), "params": p_hash, "prior": prior}).encode()
    ).hexdigest()


def diff(row: Any) -> dict[str, Any]:
    return {
        "flag_key": row["params"]["flag_key"],
        "current_value": row["prior_state"].get("value"),
        "new_value": row["params"]["value"],
        "rollback_value": row["inverse_params"]["value"],
        "prior_version": row["prior_state"].get("version"),
    }


@dataclass
class Remediation:
    engine: AsyncEngine
    settings: Settings
    registry: Registry
    keyring: Keyring
    egress: EgressClient

    async def _flag_client(
        self, conn: AsyncConnection, org_id: UUID, integration_id: UUID
    ) -> FlagClient:
        usable, unusable = await usable_integrations(
            conn, org_id, self.registry, self.keyring, frozenset({ConnectorKind.FLAG_SERVICE})
        )
        integ = next((u for u in usable if u.id == integration_id), None)
        if integ is None:
            reason = next(
                (u.reason for u in unusable if u.id == integration_id),
                "integration missing or disabled",
            )
            raise RemediationError(
                409, "target_unavailable", f"The remediation target is not usable: {reason}"
            )
        return FlagClient(self.egress, integ.connector, integ.credential)

    async def _read(self, client: FlagClient, key: str) -> FlagState:
        try:
            return await client.get(key)
        except FlagError as exc:
            raise RemediationError(
                502, "target_error", f"Could not read the target state ({exc.category})"
            ) from None

    # --- proposal ----------------------------------------------------------------------------

    async def propose(
        self,
        org_id: UUID,
        actor_user: UUID,
        actor: Actor,
        *,
        incident_id: UUID,
        action_id: UUID,
        investigation_id: UUID | None,
        reason: str,
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        async with tenant_tx(self.engine, org_id, actor_user) as c:
            action = (
                (
                    await c.execute(
                        select(remediation_actions).where(
                            remediation_actions.c.org_id == org_id,
                            remediation_actions.c.id == action_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if action is None or not action["enabled"]:
                raise RemediationError(404, "not_found", "Catalog action not found or disabled")
            if evidence_ids:
                if investigation_id is None:
                    raise RemediationError(
                        400, "invalid_evidence", "Evidence citations need an investigation"
                    )
                found = {
                    r[0]
                    for r in await c.execute(
                        select(evidence.c.evidence_id).where(
                            evidence.c.org_id == org_id,
                            evidence.c.investigation_id == investigation_id,
                            evidence.c.evidence_id.in_(evidence_ids),
                        )
                    )
                }
                missing = sorted(set(evidence_ids) - found)
                if missing:
                    raise RemediationError(
                        400, "invalid_evidence", f"Unknown evidence IDs: {', '.join(missing[:5])}"
                    )
            client = await self._flag_client(c, org_id, action["integration_id"])
        prior = await self._read(client, action["flag_key"])
        if prior.value == action["target_value"]:
            raise RemediationError(
                409, "already_applied", "The target is already in the requested state"
            )
        params = {"flag_key": action["flag_key"], "value": action["target_value"]}
        inverse = {"flag_key": action["flag_key"], "value": prior.value}
        p_hash = params_hash(action_id, action["integration_id"], params, inverse)
        async with tenant_tx(self.engine, org_id, actor_user) as c:
            try:
                async with c.begin_nested():
                    row = (
                        (
                            await c.execute(
                                insert(remediation_proposals)
                                .values(
                                    org_id=org_id,
                                    incident_id=incident_id,
                                    investigation_id=investigation_id,
                                    action_id=action_id,
                                    params=params,
                                    inverse_params=inverse,
                                    prior_state=prior.as_dict(),
                                    params_hash=p_hash,
                                    reason=reason,
                                    evidence_ids=evidence_ids,
                                    risk=action["risk"],
                                    expected_impact=action["expected_impact"],
                                    proposed_by=actor_user,
                                )
                                .returning(remediation_proposals)
                            )
                        )
                        .mappings()
                        .one()
                    )
            except IntegrityError:
                raise RemediationError(
                    409, "already_proposed", "An open proposal for this action already exists"
                ) from None
            await audit.record(
                c,
                org_id,
                actor,
                Action.REMEDIATION_PROPOSED,
                target_type="remediation",
                target_id=row["id"],
                details={
                    "incident_id": str(incident_id),
                    "action_id": str(action_id),
                    "diff": diff(row),
                    "params_hash": p_hash,
                },
            )
        return dict(row)

    # --- approval -----------------------------------------------------------------------------

    async def approve(
        self, org_id: UUID, approver: UUID, actor: Actor, proposal_id: UUID
    ) -> dict[str, Any]:
        async with tenant_tx(self.engine, org_id, approver) as c:
            row = await self._lock(c, org_id, proposal_id)
            if row["status"] != "proposed":
                raise RemediationError(
                    409,
                    "invalid_state",
                    f"Only proposed actions can be approved (status {row['status']})",
                )
            org = await self._org(c, org_id)
            if row["proposed_by"] == approver and not org.remediation_allow_self_approval:
                raise RemediationError(
                    403,
                    "two_person_rule",
                    "The approver must be a different person from the proposer",
                )
            action = await self._action(c, org_id, row["action_id"])
            client = await self._flag_client(c, org_id, action["integration_id"])
        current = await self._read(client, row["params"]["flag_key"])
        async with tenant_tx(self.engine, org_id, approver) as c:
            row = await self._lock(c, org_id, proposal_id)
            if row["status"] != "proposed":
                raise RemediationError(
                    409, "invalid_state", "The proposal changed while it was being approved"
                )
            if current.as_dict() != row["prior_state"]:
                await c.execute(
                    update(remediation_proposals)
                    .where(
                        remediation_proposals.c.org_id == org_id,
                        remediation_proposals.c.id == proposal_id,
                    )
                    .values(
                        status="expired",
                        updated_at=func.now(),
                        execution={
                            "reason": "target state changed before approval",
                            "observed": current.as_dict(),
                        },
                    )
                )
                await audit.record(
                    c,
                    org_id,
                    actor,
                    Action.REMEDIATION_REJECTED,
                    target_type="remediation",
                    target_id=proposal_id,
                    details={
                        "reason": "stale: target state changed since the proposal",
                        "observed": current.as_dict(),
                    },
                )
                stale = True
            else:
                stale = False
        if stale:  # raised only after the expiry and its audit record are committed
            raise RemediationError(
                409, "stale_proposal", "The target changed since the proposal; propose again"
            )
        async with tenant_tx(self.engine, org_id, approver) as c:
            row = await self._lock(c, org_id, proposal_id)
            if row["status"] != "proposed":
                raise RemediationError(
                    409, "invalid_state", "The proposal changed while it was being approved"
                )
            a_hash = approval_hash(proposal_id, row["params_hash"], row["prior_state"])
            expires = datetime.now(UTC) + timedelta(
                minutes=self.settings.remediation_approval_minutes
            )
            row = (
                (
                    await c.execute(
                        update(remediation_proposals)
                        .where(
                            remediation_proposals.c.org_id == org_id,
                            remediation_proposals.c.id == proposal_id,
                        )
                        .values(
                            status="approved",
                            approval_hash=a_hash,
                            approved_by=approver,
                            approved_at=func.now(),
                            approval_expires_at=expires,
                            updated_at=func.now(),
                        )
                        .returning(remediation_proposals)
                    )
                )
                .mappings()
                .one()
            )
            await audit.record(
                c,
                org_id,
                actor,
                Action.REMEDIATION_APPROVED,
                target_type="remediation",
                target_id=proposal_id,
                details={
                    "diff": diff(row),
                    "approval_hash": a_hash,
                    "expires_at": expires.isoformat(),
                    "inverse_approved": True,
                },
            )
        return dict(row)

    async def reject(
        self, org_id: UUID, user: UUID, actor: Actor, proposal_id: UUID, reason: str
    ) -> dict[str, Any]:
        async with tenant_tx(self.engine, org_id, user) as c:
            row = await self._lock(c, org_id, proposal_id)
            if row["status"] not in ("proposed", "approved"):
                raise RemediationError(
                    409, "invalid_state", f"Cannot reject a proposal in status {row['status']}"
                )
            row = (
                (
                    await c.execute(
                        update(remediation_proposals)
                        .where(
                            remediation_proposals.c.org_id == org_id,
                            remediation_proposals.c.id == proposal_id,
                        )
                        .values(
                            status="rejected",
                            rejected_by=user,
                            rejected_reason=reason[:1000],
                            updated_at=func.now(),
                        )
                        .returning(remediation_proposals)
                    )
                )
                .mappings()
                .one()
            )
            await audit.record(
                c,
                org_id,
                actor,
                Action.REMEDIATION_REJECTED,
                target_type="remediation",
                target_id=proposal_id,
                details={"reason": reason[:300]},
            )
        return dict(row)

    # --- execution ----------------------------------------------------------------------------

    async def execute(
        self, org_id: UUID, user: UUID, actor: Actor, proposal_id: UUID
    ) -> dict[str, Any]:
        async with tenant_tx(self.engine, org_id, user) as c:
            if not (await self._org(c, org_id)).remediation_enabled:
                raise RemediationError(
                    403,
                    "remediation_disabled",
                    "Remediation execution is disabled for this organization",
                )
            row = await self._lock(c, org_id, proposal_id)
            expired = row["status"] == "approved" and row["approval_expires_at"] <= datetime.now(
                UTC
            )
            if expired:
                await self._set(c, org_id, proposal_id, status="expired")
                await audit.record(
                    c,
                    org_id,
                    actor,
                    Action.REMEDIATION_REJECTED,
                    target_type="remediation",
                    target_id=proposal_id,
                    details={"reason": "approval expired before execution"},
                )
        if expired:  # raised only after the expiry is committed
            raise RemediationError(409, "approval_expired", "The approval expired; approve again")
        async with tenant_tx(self.engine, org_id, user) as c:
            row = await self._lock(c, org_id, proposal_id)
            expected = approval_hash(proposal_id, row["params_hash"], row["prior_state"])
            recomputed_params = params_hash(
                row["action_id"],
                (await self._action(c, org_id, row["action_id"]))["integration_id"],
                row["params"],
                row["inverse_params"],
            )
            claimed = (
                (
                    await c.execute(
                        update(remediation_proposals)
                        .where(
                            remediation_proposals.c.org_id == org_id,
                            remediation_proposals.c.id == proposal_id,
                            remediation_proposals.c.status == "approved",
                            remediation_proposals.c.approval_hash == expected,
                            remediation_proposals.c.params_hash == recomputed_params,
                            remediation_proposals.c.approval_expires_at > func.now(),
                        )
                        .values(status="executing", executed_by=user, updated_at=func.now())
                        .returning(remediation_proposals)
                    )
                )
                .mappings()
                .first()
            )
            if claimed is None:
                raise RemediationError(
                    409,
                    "not_executable",
                    "No valid approval for exactly these parameters (replayed, tampered or not approved)",
                )
            action = await self._action(c, org_id, claimed["action_id"])
            client = await self._flag_client(c, org_id, action["integration_id"])
            await audit.record(
                c,
                org_id,
                actor,
                Action.REMEDIATION_EXECUTED,
                target_type="remediation",
                target_id=proposal_id,
                details={"phase": "started", "diff": diff(claimed)},
            )
        key, value = claimed["params"]["flag_key"], claimed["params"]["value"]
        result: dict[str, Any]
        try:
            current = await client.get(key)
            if current.as_dict() != claimed["prior_state"]:
                result = {
                    "ok": False,
                    "reason": "target state changed since approval",
                    "observed": current.as_dict(),
                }
            else:
                after = await client.set(key, value, f"aegis:{user}", current.version)
                result = {"ok": True, "state_after": after.as_dict()}
        except FlagError as exc:
            # The write may or may not have happened: never retried, recorded for inspection.
            result = {
                "ok": False,
                "reason": f"target error ({exc.category})",
                "uncertain": exc.category in ("timeout", "connect", "http"),
            }
        async with tenant_tx(self.engine, org_id, user) as c:
            values: dict[str, Any] = {
                "execution": {**result, "at": datetime.now(UTC).isoformat()},
                "executed_at": func.now(),
            }
            if result["ok"]:
                values.update(
                    status="verifying",
                    verify_after=datetime.now(UTC)
                    + timedelta(seconds=self.settings.remediation_verify_delay_seconds),
                )
            else:
                values.update(status="failed")
            finished = await self._finish(c, org_id, proposal_id, "executing", **values)
            if finished is None:
                return await self._keep_reconciled(
                    c, org_id, actor, proposal_id, Action.REMEDIATION_EXECUTED, result
                )
            await audit.record(
                c,
                org_id,
                actor,
                Action.REMEDIATION_EXECUTED,
                target_type="remediation",
                target_id=proposal_id,
                details={
                    "phase": "finished",
                    **{k: v for k, v in result.items() if k != "observed"},
                },
            )
        return finished

    async def rollback(
        self, org_id: UUID, user: UUID, actor: Actor, proposal_id: UUID
    ) -> dict[str, Any]:
        async with tenant_tx(self.engine, org_id, user) as c:
            if not (await self._org(c, org_id)).remediation_enabled:
                raise RemediationError(
                    403,
                    "remediation_disabled",
                    "Remediation execution is disabled for this organization",
                )
            claimed = (
                (
                    await c.execute(
                        update(remediation_proposals)
                        .where(
                            remediation_proposals.c.org_id == org_id,
                            remediation_proposals.c.id == proposal_id,
                            remediation_proposals.c.status.in_(ROLLBACKABLE),
                        )
                        .values(status="rolling_back", rolled_back_by=user, updated_at=func.now())
                        .returning(remediation_proposals)
                    )
                )
                .mappings()
                .first()
            )
            if claimed is None:
                raise RemediationError(
                    409, "not_rollbackable", "Only executed actions can be rolled back (once)"
                )
            action = await self._action(c, org_id, claimed["action_id"])
            client = await self._flag_client(c, org_id, action["integration_id"])
        expected = (claimed["execution"] or {}).get("state_after") or {}
        key, value = claimed["inverse_params"]["flag_key"], claimed["inverse_params"]["value"]
        result: dict[str, Any]
        try:
            current = await client.get(key)
            if current.as_dict() != expected:
                result = {
                    "ok": False,
                    "reason": "target state changed since execution",
                    "observed": current.as_dict(),
                }
            elif value is None:
                result = {
                    "ok": False,
                    "reason": "the prior state had no value; manual rollback required",
                }
            else:
                after = await client.set(key, value, f"aegis:{user}:rollback", current.version)
                result = {"ok": True, "state_after": after.as_dict()}
        except FlagError as exc:
            result = {
                "ok": False,
                "reason": f"target error ({exc.category})",
                "uncertain": exc.category in ("timeout", "connect", "http"),
            }
        async with tenant_tx(self.engine, org_id, user) as c:
            finished = await self._finish(
                c,
                org_id,
                proposal_id,
                "rolling_back",
                status="rolled_back" if result["ok"] else "rollback_failed",
                rollback={**result, "at": datetime.now(UTC).isoformat()},
                rolled_back_at=func.now(),
            )
            if finished is None:
                return await self._keep_reconciled(
                    c, org_id, actor, proposal_id, Action.REMEDIATION_ROLLED_BACK, result
                )
            await audit.record(
                c,
                org_id,
                actor,
                Action.REMEDIATION_ROLLED_BACK,
                target_type="remediation",
                target_id=proposal_id,
                details={k: v for k, v in result.items() if k != "observed"},
            )
        return finished

    # --- helpers --------------------------------------------------------------------------------

    async def _lock(self, c: AsyncConnection, org_id: UUID, proposal_id: UUID) -> Any:
        row = (
            (
                await c.execute(
                    select(remediation_proposals)
                    .where(
                        remediation_proposals.c.org_id == org_id,
                        remediation_proposals.c.id == proposal_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise RemediationError(404, "not_found", "Remediation proposal not found")
        return row

    async def _action(self, c: AsyncConnection, org_id: UUID, action_id: UUID) -> Any:
        return (
            (
                await c.execute(
                    select(remediation_actions).where(
                        remediation_actions.c.org_id == org_id,
                        remediation_actions.c.id == action_id,
                    )
                )
            )
            .mappings()
            .one()
        )

    async def _org(self, c: AsyncConnection, org_id: UUID) -> Any:
        return read_settings(
            (
                await c.execute(
                    select(organizations.c.settings).where(organizations.c.id == org_id)
                )
            ).scalar_one()
        )

    async def _set(
        self, c: AsyncConnection, org_id: UUID, proposal_id: UUID, **values: Any
    ) -> dict[str, Any]:
        row = (
            (
                await c.execute(
                    update(remediation_proposals)
                    .where(
                        remediation_proposals.c.org_id == org_id,
                        remediation_proposals.c.id == proposal_id,
                    )
                    .values(updated_at=func.now(), **values)
                    .returning(remediation_proposals)
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def _finish(
        self, c: AsyncConnection, org_id: UUID, proposal_id: UUID, claimed: str, **values: Any
    ) -> dict[str, Any] | None:
        """Final write of execute()/rollback(): compare-and-set on the status the call claimed.
        ``None`` (0 rows) means the row left that status while the target request was in
        flight: the stuck-row sweeper reconciled it. The caller must not overwrite it."""
        row = (
            (
                await c.execute(
                    update(remediation_proposals)
                    .where(
                        remediation_proposals.c.org_id == org_id,
                        remediation_proposals.c.id == proposal_id,
                        remediation_proposals.c.status == claimed,
                    )
                    .values(updated_at=func.now(), **values)
                    .returning(remediation_proposals)
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else dict(row)

    async def _keep_reconciled(
        self,
        c: AsyncConnection,
        org_id: UUID,
        actor: Actor,
        proposal_id: UUID,
        action: Action,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """The sweeper's record stays on the row. The late outcome, including the target state
        it saw or produced, goes to the audit log so it is not lost; the current row is
        returned."""
        row = dict(await self._lock(c, org_id, proposal_id))
        await audit.record(
            c,
            org_id,
            actor,
            action,
            target_type="remediation",
            target_id=proposal_id,
            details={"phase": "finished_late", "kept_status": row["status"], **result},
        )
        return row


async def integration_kind(c: AsyncConnection, org_id: UUID, integration_id: UUID) -> str | None:
    kind: str | None = (
        await c.execute(
            select(integrations.c.kind).where(
                integrations.c.org_id == org_id, integrations.c.id == integration_id
            )
        )
    ).scalar_one_or_none()
    return kind
