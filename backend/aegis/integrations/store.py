"""Tenant integrations: rows referencing operator connectors, with sealed credentials.

``usable_integrations`` is what collectors and the connectivity test call: it returns only
enabled integrations whose connector is (still) registered *and bound to this organization*,
each with its credential decrypted under the (org, connector, integration) binding. A row
whose ciphertext was moved or altered fails decryption and is reported as unusable — it is
never sent anywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Table, update
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncConnection

from aegis.db.tables import integrations, organizations
from aegis.integrations.egress import NO_CREDENTIAL, Credential
from aegis.integrations.registry import Connector, ConnectorKind, Registry, RegistryError
from aegis.security.crypto import DecryptionFailed, Keyring, Sealed, binding


@dataclass(frozen=True)
class UsableIntegration:
    id: UUID
    name: str
    connector: Connector
    credential: Credential


@dataclass(frozen=True)
class Unusable:
    id: UUID
    connector_id: str
    reason: str


def seal_credential(
    keyring: Keyring, org_id: UUID, connector_id: str, integration_id: UUID, cred: dict[str, Any]
) -> tuple[str, bytes]:
    sealed = keyring.seal(
        json.dumps(cred, sort_keys=True).encode(), binding(org_id, connector_id, integration_id)
    )
    return sealed.kid, sealed.blob


def open_credential(keyring: Keyring, row: Any) -> Credential:
    if row["credential_type"] == "none" or row["credential_blob"] is None:
        return NO_CREDENTIAL
    raw = keyring.open(
        Sealed(row["credential_kid"], bytes(row["credential_blob"])),
        binding(row["org_id"], row["connector_id"], row["id"]),
    )
    data = json.loads(raw)
    kind = row["credential_type"]
    if kind == "bearer":
        return Credential("bearer", token=data["token"])
    if kind == "basic":
        return Credential("basic", username=data["username"], password=data["password"])
    return Credential("header", header_name=data["name"], header_value=data["value"])


async def org_slug(conn: AsyncConnection, org_id: UUID) -> str:
    slug: str = (
        await conn.execute(sa_select(organizations.c.slug).where(organizations.c.id == org_id))
    ).scalar_one()
    return slug


async def usable_integrations(
    conn: AsyncConnection,
    org_id: UUID,
    registry: Registry,
    keyring: Keyring,
    kinds: frozenset[ConnectorKind] | None = None,
) -> tuple[list[UsableIntegration], list[Unusable]]:
    slug = await org_slug(conn, org_id)
    rows = (
        (
            await conn.execute(
                sa_select(integrations).where(
                    integrations.c.org_id == org_id, integrations.c.enabled.is_(True)
                )
            )
        )
        .mappings()
        .all()
    )
    usable: list[UsableIntegration] = []
    unusable: list[Unusable] = []
    for row in rows:
        try:
            connector = registry.resolve_for_org(row["connector_id"], slug)
        except RegistryError:
            unusable.append(
                Unusable(
                    row["id"], row["connector_id"], "connector not registered for this organization"
                )
            )
            continue
        if kinds is not None and connector.kind not in kinds:
            continue
        try:
            cred = open_credential(keyring, row)
        except (DecryptionFailed, KeyError, ValueError):
            unusable.append(
                Unusable(
                    row["id"],
                    row["connector_id"],
                    "credential cannot be decrypted for this binding",
                )
            )
            continue
        usable.append(UsableIntegration(row["id"], row["name"], connector, cred))
    return usable, unusable


async def record_test(
    conn: AsyncConnection, table: Table, org_id: UUID, integration_id: UUID, ok: bool, detail: str
) -> None:
    await conn.execute(
        update(table)
        .where(table.c.org_id == org_id, table.c.id == integration_id)
        .values(
            last_test_status="ok" if ok else "error",
            last_test_detail=detail[:500],
            last_test_at=_now(),
        )
    )


def _now() -> Any:
    from sqlalchemy import func

    return func.now()
