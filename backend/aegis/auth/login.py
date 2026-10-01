"""Completing a login (shared by OIDC and the development login).

Identity-to-organization binding (ADR-011 amendment 1): a login never creates a membership
on its own. Memberships come from invitations — accepted here only when the identity
provider reports the email address as **verified** — or from operator tooling. A user with
no membership gets no session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from aegis import audit
from aegis.audit import Action, Actor
from aegis.auth import sessions
from aegis.config import Settings
from aegis.db.engine import system_tx, tenant_tx
from aegis.db.tables import invitations, memberships, users


class LoginRefused(Exception):
    """No session is created; the message is safe to show to the user."""


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


@dataclass(frozen=True)
class LoginResult:
    token: str
    session_id: UUID
    org_id: UUID
    user_id: UUID
    expires_at: datetime


async def _upsert_user(engine: AsyncEngine, ident: Identity) -> tuple[UUID, bool]:
    async with system_tx(engine) as c:
        user_id: UUID = (
            await c.execute(
                text("SELECT aegis.user_upsert(:i, :s, :e, :v, :n)"),
                {
                    "i": ident.issuer[:500],
                    "s": ident.subject[:500],
                    "e": ident.email.lower()[:320] if ident.email else None,
                    "v": ident.email_verified,
                    "n": (ident.name or "")[:200] or None,
                },
            )
        ).scalar_one()
    async with tenant_tx(engine, UUID(int=0), user_id) as c:  # a user may read its own row
        disabled: bool = (
            await c.execute(select(users.c.disabled).where(users.c.id == user_id))
        ).scalar_one()
    return user_id, bool(disabled)


async def _accept_invitations(engine: AsyncEngine, user_id: UUID, email: str) -> None:
    async with system_tx(engine) as c:
        pending = (
            await c.execute(text("SELECT * FROM aegis.invitations_for_email(:e)"), {"e": email})
        ).all()
    for inv in pending:
        async with tenant_tx(engine, inv.org_id, user_id) as c:
            await c.execute(
                pg_insert(memberships)
                .values(org_id=inv.org_id, user_id=user_id, role=inv.role)
                .on_conflict_do_nothing(index_elements=["org_id", "user_id"])
            )
            accepted = (
                await c.execute(
                    update(invitations)
                    .where(
                        invitations.c.org_id == inv.org_id,
                        invitations.c.id == inv.invitation_id,
                        invitations.c.accepted_at.is_(None),
                    )
                    .values(accepted_at=datetime.now(UTC), accepted_by=user_id)
                    .returning(invitations.c.id)
                )
            ).first()
            if accepted is not None:
                await audit.record(
                    c,
                    inv.org_id,
                    Actor("user", str(user_id)),
                    Action.INVITATION_ACCEPTED,
                    target_type="invitation",
                    target_id=inv.invitation_id,
                    details={"role": inv.role},
                )


async def user_orgs(engine: AsyncEngine, user_id: UUID) -> list[tuple[UUID, str, str, str]]:
    async with system_tx(engine) as c:
        rows = (
            await c.execute(text("SELECT * FROM aegis.user_memberships(:u)"), {"u": user_id})
        ).all()
    return [(r.org_id, r.slug, r.name, r.role) for r in rows]


async def complete_login(
    engine: AsyncEngine,
    settings: Settings,
    ident: Identity,
    *,
    org_slug: str | None,
    user_agent: str | None,
    method: str,
) -> LoginResult:
    user_id, disabled = await _upsert_user(engine, ident)
    if disabled:
        raise LoginRefused("This account is disabled.")
    if ident.email and ident.email_verified:
        await _accept_invitations(engine, user_id, ident.email.lower())
    orgs = await user_orgs(engine, user_id)
    if not orgs:
        raise LoginRefused(
            "You do not have access to any organization. Ask an administrator for an invitation."
        )
    chosen = next((o for o in orgs if o[1] == org_slug), None) if org_slug else orgs[0]
    if chosen is None:
        raise LoginRefused("You are not a member of that organization.")
    org_id = chosen[0]
    async with tenant_tx(engine, org_id, user_id) as c:
        token, sid, expires = await sessions.create_session(
            c, settings, org_id, user_id, user_agent
        )
        await audit.record(
            c,
            org_id,
            Actor("user", str(user_id)),
            Action.LOGIN,
            target_type="session",
            target_id=sid,
            details={"method": method, "issuer": ident.issuer},
        )
    return LoginResult(token, sid, org_id, user_id, expires)
