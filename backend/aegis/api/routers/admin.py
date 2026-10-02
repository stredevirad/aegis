"""Identity and administration: me, organization, members, invitations, API keys, audit, sources."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy import delete, func, insert, select, text, update

from aegis import audit
from aegis.api import schemas as S
from aegis.api import views
from aegis.api.deps import app_state, require, require_authenticated
from aegis.api.errors import ApiError, not_found
from aegis.api.state import AppState
from aegis.audit import Action
from aegis.auth import apikeys
from aegis.auth.login import user_orgs
from aegis.auth.principal import Principal
from aegis.auth.rbac import Permission
from aegis.db.engine import tenant_tx
from aegis.db.tables import audit_events, invitations, memberships, organizations, sessions, users
from aegis.org_settings import OrgSettings, read_settings

router = APIRouter(tags=["administration"])


# --- me ---------------------------------------------------------------------------------------


@router.get("/me", response_model=S.MeOut)
async def me(
    principal: Annotated[Principal, Depends(require_authenticated())],
    state: Annotated[AppState, Depends(app_state)],
) -> S.MeOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        org = (
            (await c.execute(select(organizations).where(organizations.c.id == principal.org_id)))
            .mappings()
            .first()
        )
        user = None
        if principal.user_id:
            user = (
                (await c.execute(select(users).where(users.c.id == principal.user_id)))
                .mappings()
                .first()
            )
    orgs = await user_orgs(state.engine, principal.user_id) if principal.user_id else []
    return S.MeOut(
        kind=principal.kind,
        org=S.OrgRef(
            id=org["id"], slug=org["slug"], name=org["name"], role=principal.role or "api_key"
        )
        if org
        else None,
        user_id=principal.user_id,
        email=user["email"] if user else None,
        display_name=user["display_name"] if user else None,
        role=principal.role,
        permissions=sorted(p.value for p in principal.permissions),
        memberships=[S.OrgRef(id=o[0], slug=o[1], name=o[2], role=o[3]) for o in orgs],
        api_key_prefix=principal.api_key_prefix,
    )


@router.get("/sources", response_model=S.SourcesOut)
async def sources(
    principal: Annotated[Principal, Depends(require(Permission.INVESTIGATION_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.SourcesOut:
    from aegis.api.routers.incidents import live_ready

    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        live = await live_ready(c, state, principal.org_id)
    return S.SourcesOut(
        live=live,
        scenarios=state.scenarios.ids(),
        note="Scenario sources produce SYNTHETIC telemetry for demonstration and pipeline testing.",
    )


# --- organization ------------------------------------------------------------------------------


@router.get("/organization", response_model=S.OrganizationOut)
async def get_org(
    principal: Annotated[Principal, Depends(require(Permission.ORG_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.OrganizationOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            (await c.execute(select(organizations).where(organizations.c.id == principal.org_id)))
            .mappings()
            .first()
        )
    if row is None:
        raise not_found("Organization")
    return S.OrganizationOut(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        settings=read_settings(row["settings"]).model_dump(mode="json"),
    )


@router.patch("/organization/settings", response_model=S.OrganizationOut)
async def update_settings(
    patch: dict[str, Any],
    principal: Annotated[Principal, Depends(require(Permission.ORG_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.OrganizationOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            (
                await c.execute(
                    select(organizations)
                    .where(organizations.c.id == principal.org_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        current = read_settings(row["settings"])
        try:
            new = OrgSettings.model_validate({**current.model_dump(mode="json"), **patch})
        except ValidationError as exc:
            raise ApiError(
                422,
                "validation_error",
                "Invalid settings",
                [
                    {"loc": [str(x) for x in e["loc"]], "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()[:20]
                ],
            ) from None
        source = new.default_source
        if (
            source is not None
            and source.kind == "scenario"
            and source.scenario not in state.scenarios.ids()
        ):
            raise ApiError(
                400, "source_unavailable", "That scenario is not available in this deployment"
            )
        stored = new.model_dump(mode="json")
        await c.execute(
            update(organizations)
            .where(organizations.c.id == principal.org_id)
            .values(settings=stored)
        )
        before = current.model_dump(mode="json")
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.ORG_SETTINGS_CHANGED,
            target_type="organization",
            target_id=principal.org_id,
            details={
                "changes": {
                    k: {"from": before.get(k), "to": v}
                    for k, v in stored.items()
                    if before.get(k) != v
                }
            },
        )
    return S.OrganizationOut(id=row["id"], slug=row["slug"], name=row["name"], settings=stored)


# --- members and invitations -----------------------------------------------------------------------


@router.get("/members", response_model=list[S.MemberOut])
async def list_members(
    principal: Annotated[Principal, Depends(require(Permission.MEMBER_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> list[S.MemberOut]:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rows = (
            (
                await c.execute(
                    select(
                        memberships.c.user_id,
                        memberships.c.role,
                        memberships.c.created_at,
                        users.c.email,
                        users.c.display_name,
                    )
                    .select_from(memberships.join(users, users.c.id == memberships.c.user_id))
                    .where(memberships.c.org_id == principal.org_id)
                    .order_by(users.c.email)
                )
            )
            .mappings()
            .all()
        )
    return [S.MemberOut(**dict(r)) for r in rows]


async def _admin_count(c: Any, org_id: UUID) -> int:
    n: int = (
        await c.execute(
            select(func.count())
            .select_from(memberships)
            .where(memberships.c.org_id == org_id, memberships.c.role == "admin")
        )
    ).scalar_one()
    return n


@router.patch("/members/{user_id}", response_model=S.MemberOut)
async def change_role(
    user_id: UUID,
    body: S.MemberUpdate,
    principal: Annotated[Principal, Depends(require(Permission.MEMBER_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.MemberOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            (
                await c.execute(
                    select(memberships)
                    .where(
                        memberships.c.org_id == principal.org_id, memberships.c.user_id == user_id
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("Member")
        if (
            row["role"] == "admin"
            and body.role != "admin"
            and await _admin_count(c, principal.org_id) <= 1
        ):
            raise ApiError(
                409, "last_admin", "An organization must keep at least one administrator"
            )
        await c.execute(
            update(memberships)
            .where(memberships.c.org_id == principal.org_id, memberships.c.user_id == user_id)
            .values(role=body.role)
        )
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.MEMBERSHIP_ROLE_CHANGED,
            target_type="user",
            target_id=user_id,
            details={"from": row["role"], "to": body.role},
        )
        u = (
            await c.execute(
                select(users.c.email, users.c.display_name).where(users.c.id == user_id)
            )
        ).first()
    return S.MemberOut(
        user_id=user_id,
        email=u.email if u else None,
        display_name=u.display_name if u else None,
        role=body.role,
        created_at=row["created_at"],
    )


@router.delete("/members/{user_id}", status_code=204)
async def remove_member(
    user_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.MEMBER_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> None:
    """Removes the membership, deletes the user's sessions in this organization and revokes the
    API keys they created here (ADR-011 amendment: revocation on membership removal)."""
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            (
                await c.execute(
                    select(memberships)
                    .where(
                        memberships.c.org_id == principal.org_id, memberships.c.user_id == user_id
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("Member")
        if row["role"] == "admin" and await _admin_count(c, principal.org_id) <= 1:
            raise ApiError(
                409, "last_admin", "An organization must keep at least one administrator"
            )
        await c.execute(
            delete(memberships).where(
                memberships.c.org_id == principal.org_id, memberships.c.user_id == user_id
            )
        )
        ended = await c.execute(
            delete(sessions)
            .where(sessions.c.org_id == principal.org_id, sessions.c.user_id == user_id)
            .returning(sessions.c.id)
        )
        revoked = await apikeys.revoke_created_by(
            c, principal.org_id, user_id, "creator's membership removed"
        )
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.MEMBERSHIP_REMOVED,
            target_type="user",
            target_id=user_id,
            details={
                "role": row["role"],
                "sessions_ended": len(ended.all()),
                "api_keys_revoked": revoked,
            },
        )


@router.get("/invitations", response_model=list[S.InvitationOut])
async def list_invitations(
    principal: Annotated[Principal, Depends(require(Permission.MEMBER_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> list[S.InvitationOut]:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rows = (
            (
                await c.execute(
                    select(invitations)
                    .where(invitations.c.org_id == principal.org_id)
                    .order_by(invitations.c.created_at.desc())
                )
            )
            .mappings()
            .all()
        )
    return [S.InvitationOut(**{f: r[f] for f in S.InvitationOut.model_fields}) for r in rows]


@router.post("/invitations", response_model=S.InvitationOut, status_code=201)
async def invite(
    body: S.InvitationCreate,
    principal: Annotated[Principal, Depends(require(Permission.MEMBER_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.InvitationOut:
    email = body.email.strip().lower()
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        existing = (
            await c.execute(
                select(invitations.c.id).where(
                    invitations.c.org_id == principal.org_id,
                    invitations.c.email == email,
                    invitations.c.accepted_at.is_(None),
                    invitations.c.revoked_at.is_(None),
                )
            )
        ).first()
        if existing is not None:
            raise ApiError(409, "already_invited", "An open invitation for this address exists")
        row = (
            (
                await c.execute(
                    insert(invitations)
                    .values(
                        org_id=principal.org_id,
                        email=email,
                        role=body.role,
                        invited_by=principal.user_id,
                        expires_at=datetime.now(UTC) + timedelta(days=body.expires_in_days),
                    )
                    .returning(invitations)
                )
            )
            .mappings()
            .one()
        )
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.INVITATION_CREATED,
            target_type="invitation",
            target_id=row["id"],
            details={"role": body.role},
        )
    return S.InvitationOut(**{f: row[f] for f in S.InvitationOut.model_fields})


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(
    invitation_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.MEMBER_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> None:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = (
            await c.execute(
                update(invitations)
                .where(
                    invitations.c.org_id == principal.org_id,
                    invitations.c.id == invitation_id,
                    invitations.c.accepted_at.is_(None),
                    invitations.c.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
                .returning(invitations.c.id)
            )
        ).first()
        if row is None:
            raise not_found("Open invitation")
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.INVITATION_REVOKED,
            target_type="invitation",
            target_id=invitation_id,
        )


# --- API keys ----------------------------------------------------------------------------------------


@router.get("/api-keys", response_model=list[S.ApiKeyOut])
async def list_api_keys(
    principal: Annotated[Principal, Depends(require(Permission.APIKEY_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> list[S.ApiKeyOut]:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rows = await apikeys.list_keys(c, principal.org_id)
    now = datetime.now(UTC)
    return [views.api_key(r, now) for r in rows]


@router.post("/api-keys", response_model=S.ApiKeyCreated, status_code=201)
async def create_api_key(
    body: S.ApiKeyCreate,
    principal: Annotated[Principal, Depends(require(Permission.APIKEY_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.ApiKeyCreated:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        secret, row = await apikeys.create(
            c,
            state.settings,
            org_id=principal.org_id,
            name=body.name,
            scopes=list(body.scopes),
            created_by=principal.user_id,
            ttl=timedelta(days=body.expires_in_days),
        )
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.API_KEY_CREATED,
            target_type="api_key",
            target_id=row["id"],
            details={
                "prefix": row["prefix"],
                "scopes": row["scopes"],
                "expires_at": row["expires_at"],
            },
        )
    return S.ApiKeyCreated(key=views.api_key(row, datetime.now(UTC)), secret=secret)


@router.post("/api-keys/{key_id}/rotate", response_model=S.ApiKeyCreated, status_code=201)
async def rotate_api_key(
    key_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.APIKEY_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.ApiKeyCreated:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rotated = await apikeys.rotate(
            c, state.settings, principal.org_id, key_id, principal.user_id
        )
        if rotated is None:
            raise not_found("Active API key")
        secret, row = rotated
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.API_KEY_ROTATED,
            target_type="api_key",
            target_id=key_id,
            details={"new_key_id": str(row["id"]), "new_prefix": row["prefix"], "grace_hours": 24},
        )
    return S.ApiKeyCreated(key=views.api_key(row, datetime.now(UTC)), secret=secret)


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.APIKEY_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> None:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        if not await apikeys.revoke(c, principal.org_id, key_id, "revoked by administrator"):
            raise not_found("Active API key")
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.API_KEY_REVOKED,
            target_type="api_key",
            target_id=key_id,
        )


# --- audit ----------------------------------------------------------------------------------------------


@router.get("/audit", response_model=S.Page[S.AuditEventOut])
async def list_audit(
    principal: Annotated[Principal, Depends(require(Permission.AUDIT_READ))],
    state: Annotated[AppState, Depends(app_state)],
    action: Annotated[str | None, Query(max_length=100)] = None,
    target_type: Annotated[str | None, Query(max_length=100)] = None,
    target_id: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
) -> S.Page[S.AuditEventOut]:
    stmt = select(audit_events).where(audit_events.c.org_id == principal.org_id)
    if action:
        stmt = stmt.where(audit_events.c.action == action)
    if target_type:
        stmt = stmt.where(audit_events.c.target_type == target_type)
    if target_id:
        stmt = stmt.where(audit_events.c.target_id == target_id)
    if before_seq:
        stmt = stmt.where(audit_events.c.seq < before_seq)
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        rows = (
            (await c.execute(stmt.order_by(audit_events.c.seq.desc()).limit(limit + 1)))
            .mappings()
            .all()
        )
    nxt = str(rows[limit - 1]["seq"]) if len(rows) > limit else None
    return S.Page[S.AuditEventOut](
        items=[views.audit_event(r) for r in rows[:limit]], next_cursor=nxt
    )


@router.get("/audit/verify", response_model=S.AuditVerifyOut)
async def verify_audit(
    principal: Annotated[Principal, Depends(require(Permission.AUDIT_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> S.AuditVerifyOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        broken: int | None = (await c.execute(text("SELECT aegis.audit_verify()"))).scalar_one()
        head = (
            await c.execute(
                select(audit_events.c.seq, audit_events.c.hash)
                .where(audit_events.c.org_id == principal.org_id)
                .order_by(audit_events.c.seq.desc())
                .limit(1)
            )
        ).first()
    return S.AuditVerifyOut(
        intact=broken is None,
        first_broken_seq=broken,
        events=int(head.seq) if head else 0,
        head_hash=bytes(head.hash).hex() if head else None,
        note=(
            "Detects modification by the application and accidental corruption. It is not "
            "tamper-evident against the database owner, who can recompute the chain."
        ),
    )
