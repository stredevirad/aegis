"""Server-side sessions (ADR-011).

The browser holds an opaque random token in an HttpOnly cookie; the database holds only its
keyed hash. Each request resolves the session through ``session_lookup`` (SECURITY
DEFINER), which joins the *current* membership — removing a membership or disabling a user
takes effect on the next request, nothing about authorization is cached in the cookie.
Sessions have an idle expiry (extended on use, throttled) and an absolute expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import insert, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from aegis.config import Settings
from aegis.db.engine import system_tx, tenant_tx
from aegis.db.tables import sessions
from aegis.security.tokens import hash_session_token, new_session_token, well_formed_session_token

COOKIE_NAME = "aegis_session"
TOUCH_INTERVAL = timedelta(seconds=60)


@dataclass(frozen=True)
class SessionInfo:
    session_id: UUID
    org_id: UUID
    user_id: UUID
    role: str


def _pepper(settings: Settings) -> bytes:
    return settings.secret_key.get_secret_value().encode()


async def create_session(
    conn: AsyncConnection, settings: Settings, org_id: UUID, user_id: UUID, user_agent: str | None
) -> tuple[str, UUID, datetime]:
    """Insert a session in the caller's tenant transaction; returns (token, id, expires_at)."""
    token = new_session_token()
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.session_absolute_minutes)
    idle = min(expires, now + timedelta(minutes=settings.session_idle_minutes))
    sid = (
        await conn.execute(
            insert(sessions)
            .values(
                token_hash=hash_session_token(_pepper(settings), token),
                org_id=org_id,
                user_id=user_id,
                idle_expires_at=idle,
                expires_at=expires,
                user_agent=(user_agent or "")[:500] or None,
            )
            .returning(sessions.c.id)
        )
    ).scalar_one()
    return token, sid, expires


async def resolve(engine: AsyncEngine, settings: Settings, token: str) -> SessionInfo | None:
    if not well_formed_session_token(token):
        return None
    async with system_tx(engine) as c:
        row = (
            await c.execute(
                text("SELECT * FROM aegis.session_lookup(:h)"),
                {"h": hash_session_token(_pepper(settings), token)},
            )
        ).first()
    if row is None or row.revoked or row.user_disabled or row.role is None:
        return None
    now = datetime.now(UTC)
    if now >= row.idle_expires_at or now >= row.expires_at:
        return None
    info = SessionInfo(row.session_id, row.org_id, row.user_id, row.role)
    if (
        row.idle_expires_at - now
        < timedelta(minutes=settings.session_idle_minutes) - TOUCH_INTERVAL
    ):
        async with tenant_tx(engine, row.org_id, row.user_id) as c:
            await c.execute(
                update(sessions)
                .where(sessions.c.id == row.session_id, sessions.c.org_id == row.org_id)
                .values(
                    last_seen_at=now,
                    idle_expires_at=min(
                        row.expires_at, now + timedelta(minutes=settings.session_idle_minutes)
                    ),
                )
            )
    return info


async def revoke(conn: AsyncConnection, org_id: UUID, session_id: UUID) -> None:
    await conn.execute(
        update(sessions)
        .where(
            sessions.c.org_id == org_id,
            sessions.c.id == session_id,
            sessions.c.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
