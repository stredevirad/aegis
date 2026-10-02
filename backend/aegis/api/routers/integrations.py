"""Connectors (operator registry, read-only for tenants) and tenant integrations.

Tenants choose a connector ID from the list bound to their organization and supply
credentials; they never provide a URL. Credentials are write-only: the API reports only
``credential_type`` and whether one is set.
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
from aegis.api.state import AppState
from aegis.audit import Action
from aegis.auth.principal import Principal
from aegis.auth.rbac import Permission
from aegis.db.engine import tenant_tx
from aegis.db.tables import integrations
from aegis.integrations.egress import EgressDenied, EgressError
from aegis.integrations.registry import ConnectorKind, RegistryError
from aegis.integrations.store import open_credential, org_slug, record_test, seal_credential
from aegis.security.crypto import DecryptionFailed

router = APIRouter(tags=["integrations"])

# Read-only health endpoints per connector kind (no side effects at the destination).
HEALTH_PATHS: dict[ConnectorKind, str] = {
    ConnectorKind.PROMETHEUS: "/api/v1/status/buildinfo",
    ConnectorKind.LOKI: "/ready",
    ConnectorKind.JAEGER: "/api/services",
    ConnectorKind.TEMPO: "/ready",
    ConnectorKind.OPENSEARCH: "/_cluster/health",
    ConnectorKind.KUBERNETES: "/version",
    ConnectorKind.FLAG_SERVICE: "/healthz",
}


class ConnectorOut(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    kind: str
    scheme: str
    host: str
    port: int


class CredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["none", "bearer", "basic", "header"]
    token: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
    username: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    password: Annotated[str, Field(min_length=1, max_length=1024)] | None = None
    header_name: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9-]{0,60}$")] | None = None
    header_value: Annotated[str, Field(min_length=1, max_length=4096)] | None = None

    def payload(self) -> dict[str, Any] | None:
        if self.type == "none":
            return None
        if self.type == "bearer" and self.token:
            return {"token": self.token}
        if self.type == "basic" and self.username and self.password:
            return {"username": self.username, "password": self.password}
        if self.type == "header" and self.header_name and self.header_value:
            if self.header_name.lower() in {
                "host",
                "cookie",
                "content-length",
                "transfer-encoding",
                "connection",
            }:
                raise ApiError(422, "validation_error", "That header name is not allowed")
            return {"name": self.header_name, "value": self.header_value}
        raise ApiError(422, "validation_error", f"Credential of type {self.type} is incomplete")


class IntegrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]
    name: Annotated[str, Field(min_length=1, max_length=100)]
    enabled: bool = True
    credential: CredentialIn = CredentialIn(type="none")


class IntegrationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    enabled: bool | None = None
    credential: CredentialIn | None = None


class IntegrationOut(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    connector_id: str
    kind: str
    name: str
    enabled: bool
    credential_type: str
    has_credential: bool
    connector_available: bool
    created_at: datetime
    updated_at: datetime
    last_test_at: datetime | None
    last_test_status: str | None
    last_test_detail: str | None


class TestOut(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: Literal["ok", "error"]
    category: str | None
    detail: str


def _out(row: Any, available: bool) -> IntegrationOut:
    return IntegrationOut(
        id=row["id"],
        connector_id=row["connector_id"],
        kind=row["kind"],
        name=row["name"],
        enabled=row["enabled"],
        credential_type=row["credential_type"],
        has_credential=row["credential_blob"] is not None,
        connector_available=available,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_test_at=row["last_test_at"],
        last_test_status=row["last_test_status"],
        last_test_detail=row["last_test_detail"],
    )


@router.get("/connectors", response_model=list[ConnectorOut])
async def list_connectors(
    principal: Annotated[Principal, Depends(require(Permission.INTEGRATION_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> list[ConnectorOut]:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        slug = await org_slug(c, principal.org_id)
    return [ConnectorOut(**conn.public_view()) for conn in state.registry.for_org(slug)]


@router.get("/integrations", response_model=list[IntegrationOut])
async def list_integrations(
    principal: Annotated[Principal, Depends(require(Permission.INTEGRATION_READ))],
    state: Annotated[AppState, Depends(app_state)],
) -> list[IntegrationOut]:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        slug = await org_slug(c, principal.org_id)
        rows = (
            (
                await c.execute(
                    select(integrations)
                    .where(integrations.c.org_id == principal.org_id)
                    .order_by(integrations.c.name)
                )
            )
            .mappings()
            .all()
        )
    available = {conn.id for conn in state.registry.for_org(slug)}
    return [_out(r, r["connector_id"] in available) for r in rows]


@router.post("/integrations", response_model=IntegrationOut, status_code=201)
async def create_integration(
    body: IntegrationCreate,
    principal: Annotated[Principal, Depends(require(Permission.INTEGRATION_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> IntegrationOut:
    cred = body.credential.payload()
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        slug = await org_slug(c, principal.org_id)
        try:
            connector = state.registry.resolve_for_org(body.connector_id, slug)
        except RegistryError:
            raise ApiError(404, "not_found", "Connector not found") from None
        exists = (
            await c.execute(
                select(integrations.c.id).where(
                    integrations.c.org_id == principal.org_id,
                    integrations.c.connector_id == connector.id,
                )
            )
        ).first()
        if exists:
            raise ApiError(409, "conflict", "An integration for this connector already exists")
        row = (
            (
                await c.execute(
                    insert(integrations)
                    .values(
                        org_id=principal.org_id,
                        connector_id=connector.id,
                        kind=connector.kind.value,
                        name=body.name,
                        enabled=body.enabled,
                        created_by=principal.user_id,
                    )
                    .returning(integrations)
                )
            )
            .mappings()
            .one()
        )
        if cred is not None:
            kid, blob = seal_credential(
                state.keyring, principal.org_id, connector.id, row["id"], cred
            )
            row = (
                (
                    await c.execute(
                        update(integrations)
                        .where(
                            integrations.c.org_id == principal.org_id,
                            integrations.c.id == row["id"],
                        )
                        .values(
                            credential_type=body.credential.type,
                            credential_kid=kid,
                            credential_blob=blob,
                        )
                        .returning(integrations)
                    )
                )
                .mappings()
                .one()
            )
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.INTEGRATION_CREATED,
            target_type="integration",
            target_id=row["id"],
            details={
                "connector_id": connector.id,
                "kind": connector.kind.value,
                "credential_type": body.credential.type,
            },
        )
    return _out(row, True)


async def _get(c: Any, org_id: UUID, integration_id: UUID, lock: bool = False) -> Any:
    stmt = select(integrations).where(
        integrations.c.org_id == org_id, integrations.c.id == integration_id
    )
    row = (await c.execute(stmt.with_for_update() if lock else stmt)).mappings().first()
    if row is None:
        raise not_found("Integration")
    return row


@router.patch("/integrations/{integration_id}", response_model=IntegrationOut)
async def update_integration(
    integration_id: UUID,
    body: IntegrationUpdate,
    principal: Annotated[Principal, Depends(require(Permission.INTEGRATION_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> IntegrationOut:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        slug = await org_slug(c, principal.org_id)
        row = await _get(c, principal.org_id, integration_id, lock=True)
        values: dict[str, Any] = {"updated_at": func.now()}
        changed: list[str] = []
        if body.name is not None:
            values["name"] = body.name
            changed.append("name")
        if body.enabled is not None:
            values["enabled"] = body.enabled
            changed.append("enabled")
        if body.credential is not None:
            cred = body.credential.payload()
            if cred is None:
                values.update(credential_type="none", credential_kid=None, credential_blob=None)
            else:
                kid, blob = seal_credential(
                    state.keyring, principal.org_id, row["connector_id"], row["id"], cred
                )
                values.update(
                    credential_type=body.credential.type, credential_kid=kid, credential_blob=blob
                )
            changed.append("credential")
        row = (
            (
                await c.execute(
                    update(integrations)
                    .where(
                        integrations.c.org_id == principal.org_id,
                        integrations.c.id == integration_id,
                    )
                    .values(**values)
                    .returning(integrations)
                )
            )
            .mappings()
            .one()
        )
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.INTEGRATION_UPDATED,
            target_type="integration",
            target_id=integration_id,
            details={"changed": changed},
        )
    return _out(row, row["connector_id"] in {x.id for x in state.registry.for_org(slug)})


@router.delete("/integrations/{integration_id}", status_code=204)
async def delete_integration(
    integration_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INTEGRATION_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> None:
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        row = await _get(c, principal.org_id, integration_id, lock=True)
        await c.execute(
            delete(integrations).where(
                integrations.c.org_id == principal.org_id, integrations.c.id == integration_id
            )
        )
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.INTEGRATION_DELETED,
            target_type="integration",
            target_id=integration_id,
            details={"connector_id": row["connector_id"]},
        )


@router.post("/integrations/{integration_id}/test", response_model=TestOut)
async def test_integration(
    integration_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.INTEGRATION_MANAGE))],
    state: Annotated[AppState, Depends(app_state)],
) -> TestOut:
    """Connectivity test through the egress client (read-only health endpoint)."""
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        slug = await org_slug(c, principal.org_id)
        row = await _get(c, principal.org_id, integration_id)
    try:
        connector = state.registry.resolve_for_org(row["connector_id"], slug)
    except RegistryError:
        raise ApiError(
            409,
            "connector_unavailable",
            "The connector is no longer registered for this organization",
        ) from None
    path = HEALTH_PATHS.get(connector.kind)
    if path is None:
        raise ApiError(
            400, "not_testable", "This kind of integration has no side-effect-free health check"
        )
    try:
        credential = open_credential(state.keyring, row)
    except (DecryptionFailed, KeyError, ValueError):
        raise ApiError(
            409, "credential_unusable", "The stored credential cannot be decrypted; set it again"
        ) from None
    result: TestOut
    try:
        await state.egress.request(
            connector, "GET", path, credential=credential, retries=0, max_bytes=256 * 1024
        )
        result = TestOut(status="ok", category=None, detail=f"{connector.kind.value} reachable")
    except EgressDenied as exc:
        result = TestOut(status="error", category="denied", detail=exc.reason[:300])
    except EgressError as exc:
        result = TestOut(status="error", category=exc.category, detail=str(exc)[:300])
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        await record_test(
            c, integrations, principal.org_id, integration_id, result.status == "ok", result.detail
        )
    return result
