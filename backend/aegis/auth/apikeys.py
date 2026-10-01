"""API keys for machines (ADR-011, security model C3).

* Shown once at creation; stored as an HMAC (``aegis.security.tokens``).
* Scoped to one organization (the key, never the payload, determines the organization).
* Scopes from a closed set; expiry is mandatory (default 90 days, max 365).
* Rotation issues a new key with the same name/scopes and gives the old key a short grace
  period (default 24 h) so senders can be reconfigured without dropping alerts.
* Revocation is immediate. Keys created by a user whose membership is removed are revoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from aegis.config import Settings
from aegis.db.engine import system_tx, tenant_tx
from aegis.db.tables import api_keys
from aegis.security.tokens import hash_api_key, new_api_key, parse_api_key

DEFAULT_TTL = timedelta(days=90)
MAX_TTL = timedelta(days=365)
ROTATION_GRACE = timedelta(hours=24)
LAST_USED_GRANULARITY = timedelta(minutes=5)


@dataclass(frozen=True)
class KeyInfo:
    key_id: UUID
    org_id: UUID
    prefix: str
    scopes: list[str]


def _pepper(settings: Settings) -> bytes:
    return settings.secret_key.get_secret_value().encode()


async def create(
    conn: AsyncConnection,
    settings: Settings,
    *,
    org_id: UUID,
    name: str,
    scopes: list[str],
    created_by: UUID | None,
    ttl: timedelta = DEFAULT_TTL,
    rotated_from: UUID | None = None,
) -> tuple[str, dict[str, Any]]:
    if ttl <= timedelta(0) or ttl > MAX_TTL:
        raise ValueError("expiry must be between now and 365 days")
    key = new_api_key(_pepper(settings))
    row = (
        (
            await conn.execute(
                insert(api_keys)
                .values(
                    org_id=org_id,
                    name=name,
                    prefix=key.prefix,
                    secret_hash=key.secret_hash,
                    scopes=sorted(set(scopes)),
                    created_by=created_by,
                    expires_at=datetime.now(UTC) + ttl,
                    rotated_from=rotated_from,
                )
                .returning(*[c for c in api_keys.c if c.name != "secret_hash"])
            )
        )
        .mappings()
        .one()
    )
    return key.plaintext, dict(row)


async def resolve(engine: AsyncEngine, settings: Settings, presented: str) -> KeyInfo | None:
    prefix = parse_api_key(presented)
    if prefix is None:
        return None
    async with system_tx(engine) as c:
        row = (
            await c.execute(
                text("SELECT * FROM aegis.api_key_lookup(:h)"),
                {"h": hash_api_key(_pepper(settings), presented)},
            )
        ).first()
    if row is None or row.revoked or row.expires_at <= datetime.now(UTC):
        return None
    return KeyInfo(row.key_id, row.org_id, row.prefix, list(row.scopes))


async def touch(engine: AsyncEngine, key: KeyInfo) -> None:
    """Record use at a coarse granularity (no write per request during ingestion bursts)."""
    async with tenant_tx(engine, key.org_id) as c:
        cutoff = datetime.now(UTC) - LAST_USED_GRANULARITY
        await c.execute(
            update(api_keys)
            .where(
                api_keys.c.org_id == key.org_id,
                api_keys.c.id == key.key_id,
                (api_keys.c.last_used_at.is_(None)) | (api_keys.c.last_used_at < cutoff),
            )
            .values(last_used_at=datetime.now(UTC))
        )


async def get(conn: AsyncConnection, org_id: UUID, key_id: UUID) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                select(*[c for c in api_keys.c if c.name != "secret_hash"]).where(
                    api_keys.c.org_id == org_id, api_keys.c.id == key_id
                )
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def list_keys(conn: AsyncConnection, org_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(
        select(*[c for c in api_keys.c if c.name != "secret_hash"])
        .where(api_keys.c.org_id == org_id)
        .order_by(api_keys.c.created_at.desc())
    )
    return [dict(r) for r in rows.mappings()]


async def revoke(conn: AsyncConnection, org_id: UUID, key_id: UUID, reason: str) -> bool:
    row = (
        await conn.execute(
            update(api_keys)
            .where(
                api_keys.c.org_id == org_id,
                api_keys.c.id == key_id,
                api_keys.c.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC), revoked_reason=reason[:200])
            .returning(api_keys.c.id)
        )
    ).first()
    return row is not None


async def rotate(
    conn: AsyncConnection, settings: Settings, org_id: UUID, key_id: UUID, by: UUID | None
) -> tuple[str, dict[str, Any]] | None:
    old = (
        (
            await conn.execute(
                select(api_keys)
                .where(
                    api_keys.c.org_id == org_id,
                    api_keys.c.id == key_id,
                    api_keys.c.revoked_at.is_(None),
                )
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if old is None or old["expires_at"] <= datetime.now(UTC):
        return None
    plaintext, new = await create(
        conn,
        settings,
        org_id=org_id,
        name=old["name"],
        scopes=list(old["scopes"]),
        created_by=by,
        rotated_from=key_id,
    )
    grace_end = min(old["expires_at"], datetime.now(UTC) + ROTATION_GRACE)
    await conn.execute(
        update(api_keys)
        .where(api_keys.c.org_id == org_id, api_keys.c.id == key_id)
        .values(expires_at=grace_end)
    )
    return plaintext, new


async def revoke_created_by(conn: AsyncConnection, org_id: UUID, user_id: UUID, reason: str) -> int:
    rows = await conn.execute(
        update(api_keys)
        .where(
            api_keys.c.org_id == org_id,
            api_keys.c.created_by == user_id,
            api_keys.c.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason[:200])
        .returning(api_keys.c.id)
    )
    return len(rows.all())
