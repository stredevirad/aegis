"""Remediation: action catalog (admin), suggestions, proposals, approval, execution, rollback.

Suggestions are deterministic: catalog actions whose ``service`` is a root cause of the
incident's latest completed investigation (or the incident's own service). The model never
proposes, and nothing here accepts flag keys or values from the request: they come from the
catalog entry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, insert, select, update

from aegis import audit
from aegis.api.deps import app_state, require
from aegis.api.errors import ApiError, not_found
from aegis.api.queries import get_incident
from aegis.api.state import AppState
from aegis.audit import Action
from aegis.auth.principal import Principal
from aegis.auth.rbac import Permission
from aegis.db.engine import tenant_tx
from aegis.db.tables import investigations, remediation_actions, remediation_proposals
from aegis.remediation.service import Remediation, RemediationError, diff, integration_kind

router = APIRouter(tags=["remediation"])


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=1000)] = ""
    integration_id: UUID
    service: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    flag_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,100}$")]
    target_value: Annotated[str, Field(max_length=100)]
    risk: Literal["low", "medium", "high"]
    expected_impact: Annotated[str, Field(max_length=1000)] = ""
    enabled: bool = True


class ActionOut(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    name: str
    description: str
    kind: str
    integration_id: UUID
    service: str | None
    flag_key: str
    target_value: str
    risk: str
    expected_impact: str
    enabled: bool
    created_at: datetime


class SuggestionOut(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: ActionOut
    why: str
    investigation_id: UUID | None
    evidence_ids: list[str]


class ProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: UUID
    investigation_id: UUID | None = None
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_ids: Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=20)] = []


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class ProposalOut(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    incident_id: UUID
    investigation_id: UUID | None
    action_id: UUID
    status: str
    diff: dict[str, Any]
    params_hash: str
    reason: str
    evidence_ids: list[str]
    risk: str
    expected_impact: str
    proposed_by: UUID | None
    proposed_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    approval_expires_at: datetime | None
    rejected_by: UUID | None
    rejected_reason: str | None
    executed_by: UUID | None
    executed_at: datetime | None
    execution: dict[str, Any]
    verification: dict[str, Any]
    rolled_back_by: UUID | None
    rolled_back_at: datetime | None
    rollback: dict[str, Any]
    verify_after: datetime | None


def _action_out(r: Any) -> ActionOut:
    return ActionOut(**{f: r[f] for f in ActionOut.model_fields})


def _proposal_out(r: Any) -> ProposalOut:
    return ProposalOut(diff=diff(r), **{f: r[f] for f in ProposalOut.model_fields if f != "diff"})


def _service(state: AppState) -> Remediation:
    return Remediation(state.engine, state.settings, state.registry, state.keyring, state.egress)


def _raise(exc: RemediationError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message)


# --- catalog -----------------------------------------------------------------------------------


@router.get("/remediation/actions", response_model=list[ActionOut])
async def list_actions(
    principal: Annotated[Principal, Depends(require(Permission.INCIDENT_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> list[ActionOut]:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rows = (
            (
                await c.execute(
                    select(remediation_actions)
                    .where(remediation_actions.c.org_id == principal.org_id)
                    .order_by(remediation_actions.c.name)
                )
            )
            .mappings()
            .all()
        )
    return [_action_out(r) for r in rows]


@router.post("/remediation/actions", response_model=ActionOut, status_code=201)
async def create_action(
    body: ActionIn,
    principal: Annotated[Principal, Depends(require(Permission.REMEDIATION_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> ActionOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        if await integration_kind(c, principal.org_id, body.integration_id) != "flag_service":
            raise ApiError(
                400,
                "invalid_target",
                "integration_id must be one of this organization's flag-service integrations",
            )
        exists = (
            await c.execute(
                select(remediation_actions.c.id).where(
                    remediation_actions.c.org_id == principal.org_id,
                    remediation_actions.c.name == body.name,
                )
            )
        ).first()
        if exists:
            raise ApiError(409, "conflict", "An action with this name exists")
        row = (
            (
                await c.execute(
                    insert(remediation_actions)
                    .values(
                        org_id=principal.org_id,
                        kind="flag_set",
                        created_by=principal.user_id,
                        **body.model_dump(),
                    )
                    .returning(remediation_actions)
                )
            )
            .mappings()
            .one()
        )
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.CONFIG_CHANGED,
            target_type="remediation_action",
            target_id=row["id"],
            details={
                "change": "catalog action created",
                "flag_key": body.flag_key,
                "target_value": body.target_value,
                "risk": body.risk,
            },
        )
    return _action_out(row)


@router.patch("/remediation/actions/{action_id}", response_model=ActionOut)
async def toggle_action(
    action_id: UUID,
    body: dict[str, bool],
    principal: Annotated[Principal, Depends(require(Permission.REMEDIATION_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> ActionOut:
    if set(body) != {"enabled"}:
        raise ApiError(
            422,
            "validation_error",
            "Only 'enabled' can be changed; create a new action for other changes",
        )
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            (
                await c.execute(
                    update(remediation_actions)
                    .where(
                        remediation_actions.c.org_id == principal.org_id,
                        remediation_actions.c.id == action_id,
                    )
                    .values(enabled=body["enabled"])
                    .returning(remediation_actions)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("Action")
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.CONFIG_CHANGED,
            target_type="remediation_action",
            target_id=action_id,
            details={"enabled": body["enabled"]},
        )
    return _action_out(row)


@router.delete("/remediation/actions/{action_id}", status_code=204)
async def delete_action(
    action_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.REMEDIATION_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> None:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        used = (
            await c.execute(
                select(func.count())
                .select_from(remediation_proposals)
                .where(
                    remediation_proposals.c.org_id == principal.org_id,
                    remediation_proposals.c.action_id == action_id,
                )
            )
        ).scalar_one()
        if used:
            raise ApiError(409, "in_use", "The action has proposals; disable it instead")
        gone = (
            await c.execute(
                delete(remediation_actions)
                .where(
                    remediation_actions.c.org_id == principal.org_id,
                    remediation_actions.c.id == action_id,
                )
                .returning(remediation_actions.c.id)
            )
        ).first()
        if gone is None:
            raise not_found("Action")
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.CONFIG_CHANGED,
            target_type="remediation_action",
            target_id=action_id,
            details={"change": "catalog action deleted"},
        )


# --- suggestions and proposals -----------------------------------------------------------------------


@router.get("/incidents/{incident_id}/remediation-suggestions", response_model=list[SuggestionOut])
async def suggestions(
    incident_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INCIDENT_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> list[SuggestionOut]:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        inc = await get_incident(c, principal.org_id, incident_id)
        if inc is None:
            raise not_found("Incident")
        inv = (
            (
                await c.execute(
                    select(investigations)
                    .where(
                        investigations.c.org_id == principal.org_id,
                        investigations.c.incident_id == incident_id,
                        investigations.c.status == "completed",
                    )
                    .order_by(investigations.c.finished_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        actions = (
            (
                await c.execute(
                    select(remediation_actions).where(
                        remediation_actions.c.org_id == principal.org_id,
                        remediation_actions.c.enabled.is_(True),
                    )
                )
            )
            .mappings()
            .all()
        )
    roots = {
        rc["service"]: rc for rc in ((inv["verdict"] or {}).get("root_causes", []) if inv else [])
    }
    out = []
    for a in actions:
        if a["service"] and a["service"] in roots:
            rc = roots[a["service"]]
            out.append(
                SuggestionOut(
                    action=_action_out(a),
                    investigation_id=inv["id"] if inv else None,
                    why=f"{a['service']} is a root cause ({rc['mechanism']}, support {rc['support']}) in the latest investigation",
                    evidence_ids=list(rc.get("supporting_evidence_ids", []))[:10],
                )
            )
        elif a["service"] and a["service"] == inc["service"] and not roots:
            out.append(
                SuggestionOut(
                    action=_action_out(a),
                    investigation_id=None,
                    why=f"{a['service']} is the incident's service (no root cause established)",
                    evidence_ids=[],
                )
            )
    return out


@router.get("/incidents/{incident_id}/remediations", response_model=list[ProposalOut])
async def list_proposals(
    incident_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INCIDENT_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> list[ProposalOut]:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        if await get_incident(c, principal.org_id, incident_id) is None:
            raise not_found("Incident")
        rows = (
            (
                await c.execute(
                    select(remediation_proposals)
                    .where(
                        remediation_proposals.c.org_id == principal.org_id,
                        remediation_proposals.c.incident_id == incident_id,
                    )
                    .order_by(remediation_proposals.c.proposed_at.desc())
                )
            )
            .mappings()
            .all()
        )
    return [_proposal_out(r) for r in rows]


@router.post("/incidents/{incident_id}/remediations", response_model=ProposalOut, status_code=201)
async def propose(
    incident_id: UUID,
    body: ProposalIn,
    principal: Annotated[Principal, Depends(require(Permission.REMEDIATION_PROPOSE))],
    state: Annotated[AppState, Depends(app_state)],
) -> ProposalOut:
    if principal.user_id is None:
        raise ApiError(403, "forbidden", "Remediation requires a user session")
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        if await get_incident(c, principal.org_id, incident_id) is None:
            raise not_found("Incident")
    try:
        row = await _service(state).propose(
            principal.org_id,
            principal.user_id,
            principal.actor,
            incident_id=incident_id,
            action_id=body.action_id,
            investigation_id=body.investigation_id,
            reason=body.reason,
            evidence_ids=body.evidence_ids,
        )
    except RemediationError as exc:
        raise _raise(exc) from None
    return _proposal_out(row)


async def _get(state: AppState, principal: Principal, proposal_id: UUID) -> Any:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            (
                await c.execute(
                    select(remediation_proposals).where(
                        remediation_proposals.c.org_id == principal.org_id,
                        remediation_proposals.c.id == proposal_id,
                    )
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise not_found("Remediation proposal")
    return row


@router.get("/remediations/{proposal_id}", response_model=ProposalOut)
async def get_proposal(
    proposal_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INCIDENT_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> ProposalOut:
    return _proposal_out(await _get(state, principal, proposal_id))


async def _transition(
    state: AppState, principal: Principal, proposal_id: UUID, op: str, reason: str | None = None
) -> ProposalOut:
    if principal.user_id is None:
        raise ApiError(403, "forbidden", "Remediation requires a user session")
    await _get(state, principal, proposal_id)
    svc = _service(state)
    try:
        if op == "approve":
            row = await svc.approve(
                principal.org_id, principal.user_id, principal.actor, proposal_id
            )
        elif op == "reject":
            row = await svc.reject(
                principal.org_id, principal.user_id, principal.actor, proposal_id, reason or ""
            )
        elif op == "execute":
            row = await svc.execute(
                principal.org_id, principal.user_id, principal.actor, proposal_id
            )
        else:
            row = await svc.rollback(
                principal.org_id, principal.user_id, principal.actor, proposal_id
            )
    except RemediationError as exc:
        raise _raise(exc) from None
    return _proposal_out(row)


@router.post("/remediations/{proposal_id}/approve", response_model=ProposalOut)
async def approve(
    proposal_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.REMEDIATION_APPROVE))],
    state: Annotated[AppState, Depends(app_state)],
) -> ProposalOut:
    return await _transition(state, principal, proposal_id, "approve")


@router.post("/remediations/{proposal_id}/reject", response_model=ProposalOut)
async def reject(
    proposal_id: UUID,
    body: RejectIn,
    principal: Annotated[Principal, Depends(require(Permission.REMEDIATION_APPROVE))],
    state: Annotated[AppState, Depends(app_state)],
) -> ProposalOut:
    return await _transition(state, principal, proposal_id, "reject", body.reason)


@router.post("/remediations/{proposal_id}/execute", response_model=ProposalOut)
async def execute(
    proposal_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.REMEDIATION_EXECUTE))],
    state: Annotated[AppState, Depends(app_state)],
) -> ProposalOut:
    return await _transition(state, principal, proposal_id, "execute")


@router.post("/remediations/{proposal_id}/rollback", response_model=ProposalOut)
async def rollback(
    proposal_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.REMEDIATION_EXECUTE))],
    state: Annotated[AppState, Depends(app_state)],
) -> ProposalOut:
    return await _transition(state, principal, proposal_id, "rollback")
