"""Connectors and tenant integrations through the API (ADR-016, Council C's "one thing first").

Demonstrates that tenant A cannot make Aegis query tenant B's backend, Aegis's own database
or API, or the cloud metadata service — through its own configuration or through a
credential copied from another tenant.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from aegis.api.app import create_app
from aegis.config import Profile, Settings
from aegis.integrations.egress import EgressClient
from tests.api.conftest import BASE, CSRF, SCENARIOS

pytestmark = pytest.mark.db


async def _local_server():
    seen: list[dict] = []

    async def handle(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        lines = head.split("\r\n")
        seen.append(
            {
                "line": lines[0],
                "headers": {
                    k.lower(): v for k, _, v in (ln.partition(": ") for ln in lines[1:] if ln)
                },
            }
        )
        body = b'{"status":"success","data":{"version":"3.0.0"}}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], seen


def _resolver(table):
    async def resolve(host, port):
        if host not in table:
            raise OSError("NXDOMAIN")
        return table[host]

    return resolve


@pytest.fixture
async def world(tenant_factory, member_factory, app_engine, registry, tmp_path):
    a, b = await tenant_factory("ia"), await tenant_factory("ib")
    server, port, seen = await _local_server()
    connectors = tmp_path / "connectors.yaml"
    connectors.write_text(
        f"""connectors:
  - id: prom-a
    kind: prometheus
    url: http://prom-a.test:{port}
    orgs: [{a.slug}]
    allow_loopback: true
  - id: prom-b
    kind: prometheus
    url: http://prom-b.test:{port}
    orgs: [{b.slug}]
    allow_loopback: true
  - id: meta-trap
    kind: prometheus
    url: http://innocent.test:80
    orgs: [{a.slug}]
    allow_private: true
""",
        encoding="utf-8",
    )
    settings = Settings(
        profile=Profile.TEST,
        auth_mode="dev",
        public_base_url=BASE,
        allowed_origins=[BASE],
        scenario_dir=SCENARIOS,
        connectors_file=connectors,
    )
    egress = EgressClient(
        settings,
        resolver=_resolver(
            {
                "prom-a.test": ["127.0.0.1"],
                "prom-b.test": ["127.0.0.1"],
                "innocent.test": ["169.254.169.254"],
            }
        ),
    )
    app = create_app(settings, engine=app_engine, scenarios=registry, egress=egress)
    clients: list[httpx.AsyncClient] = []

    async def login(tenant, role):
        email = await member_factory(tenant, role)
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
        clients.append(c)
        assert (
            await c.post("/auth/dev/login", json={"email": email, "org": tenant.slug}, headers=CSRF)
        ).status_code == 200
        return c

    yield {"a": a, "b": b, "login": login, "seen": seen}
    for c in clients:
        await c.aclose()
    server.close()
    await egress.aclose()


async def test_connectors_are_scoped_and_urls_are_never_tenant_input(world):
    admin_a = await world["login"](world["a"], "admin")
    ids = [c["id"] for c in (await admin_a.get("/api/v1/connectors")).json()]
    assert ids == ["meta-trap", "prom-a"]
    # tenant A cannot bind tenant B's connector
    r = await admin_a.post(
        "/api/v1/integrations", json={"connector_id": "prom-b", "name": "x"}, headers=CSRF
    )
    assert r.status_code == 404
    # there is no way to pass a URL
    r = await admin_a.post(
        "/api/v1/integrations",
        json={"connector_id": "prom-a", "name": "x", "url": "http://169.254.169.254/"},
        headers=CSRF,
    )
    assert r.status_code == 422


async def test_credentials_are_write_only_and_encrypted(world, su_conn):
    admin_a = await world["login"](world["a"], "admin")
    r = await admin_a.post(
        "/api/v1/integrations",
        json={
            "connector_id": "prom-a",
            "name": "Prometheus",
            "credential": {"type": "bearer", "token": "tok-SECRET-123"},
        },
        headers=CSRF,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert (
        body["has_credential"] is True
        and body["credential_type"] == "bearer"
        and "tok-SECRET" not in r.text
    )
    assert "tok-SECRET" not in (await admin_a.get("/api/v1/integrations")).text
    blob = await su_conn.fetchval(
        "SELECT credential_blob FROM aegis.integrations WHERE id = $1", uuid.UUID(body["id"])
    )
    assert b"tok-SECRET" not in bytes(blob)
    # the connectivity test goes through the egress client, with the credential in a header
    t = await admin_a.post(f"/api/v1/integrations/{body['id']}/test", headers=CSRF)
    assert t.json() == {"status": "ok", "category": None, "detail": "prometheus reachable"}
    req = world["seen"][-1]
    assert (
        req["line"].startswith("GET /api/v1/status/buildinfo")
        and req["headers"]["authorization"] == "Bearer tok-SECRET-123"
    )
    assert req["headers"]["host"].startswith("prom-a.test:")
    audit = (await admin_a.get("/api/v1/audit", params={"action": "integration.created"})).json()[
        "items"
    ]
    assert audit and "tok-SECRET" not in str(audit)


async def test_metadata_destination_is_refused_at_request_time(world):
    admin_a = await world["login"](world["a"], "admin")
    r = await admin_a.post(
        "/api/v1/integrations", json={"connector_id": "meta-trap", "name": "m"}, headers=CSRF
    )
    t = await admin_a.post(f"/api/v1/integrations/{r.json()['id']}/test", headers=CSRF)
    assert t.json()["status"] == "error" and t.json()["category"] == "denied"
    assert "forbidden address" in t.json()["detail"]


async def test_copied_ciphertext_cannot_be_used_by_another_tenant(world, su_conn):
    admin_a = await world["login"](world["a"], "admin")
    admin_b = await world["login"](world["b"], "admin")
    a_int = (
        await admin_a.post(
            "/api/v1/integrations",
            json={
                "connector_id": "prom-a",
                "name": "A",
                "credential": {"type": "bearer", "token": "a-secret"},
            },
            headers=CSRF,
        )
    ).json()
    b_int = (
        await admin_b.post(
            "/api/v1/integrations",
            json={
                "connector_id": "prom-b",
                "name": "B",
                "credential": {"type": "bearer", "token": "b-secret"},
            },
            headers=CSRF,
        )
    ).json()
    # an attacker with database write access copies A's ciphertext into B's row
    await su_conn.execute(
        "UPDATE aegis.integrations AS b SET credential_blob = a.credential_blob, credential_kid = a.credential_kid "
        "FROM aegis.integrations AS a WHERE a.id = $1 AND b.id = $2",
        uuid.UUID(a_int["id"]),
        uuid.UUID(b_int["id"]),
    )
    before = len(world["seen"])
    r = await admin_b.post(f"/api/v1/integrations/{b_int['id']}/test", headers=CSRF)
    assert r.status_code == 409 and r.json()["error"]["code"] == "credential_unusable"
    assert len(world["seen"]) == before  # nothing was sent anywhere


async def test_cross_tenant_integration_access_and_rbac(world):
    admin_a = await world["login"](world["a"], "admin")
    admin_b = await world["login"](world["b"], "admin")
    created = (
        await admin_a.post(
            "/api/v1/integrations", json={"connector_id": "prom-a", "name": "A"}, headers=CSRF
        )
    ).json()
    for method, path, body in [
        ("PATCH", f"/api/v1/integrations/{created['id']}", {"enabled": False}),
        ("DELETE", f"/api/v1/integrations/{created['id']}", None),
        ("POST", f"/api/v1/integrations/{created['id']}/test", None),
    ]:
        assert (await admin_b.request(method, path, json=body, headers=CSRF)).status_code == 404
    assert (await admin_b.get("/api/v1/integrations")).json() == []
    investigator = await world["login"](world["a"], "investigator")
    assert (await investigator.get("/api/v1/integrations")).status_code == 200
    assert (
        await investigator.post(
            "/api/v1/integrations", json={"connector_id": "prom-a", "name": "x"}, headers=CSRF
        )
    ).status_code == 403
    viewer = await world["login"](world["a"], "viewer")
    assert (await viewer.get("/api/v1/connectors")).status_code == 403
    # update: disabling and replacing the credential, then deleting
    upd = await admin_a.patch(
        f"/api/v1/integrations/{created['id']}",
        json={"enabled": False, "credential": {"type": "basic", "username": "u", "password": "p"}},
        headers=CSRF,
    )
    assert upd.json()["enabled"] is False and upd.json()["credential_type"] == "basic"
    assert (
        await admin_a.delete(f"/api/v1/integrations/{created['id']}", headers=CSRF)
    ).status_code == 204


async def test_registry_pointing_at_aegis_database_refuses_to_start(app_engine, registry, tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text(
        "connectors:\n  - id: db\n    kind: prometheus\n    url: http://db.internal:5432\n    orgs: [x]\n"
    )
    from aegis.api.app import UnsafeConfiguration

    settings = Settings(
        profile=Profile.TEST,
        connectors_file=f,
        database_url="postgresql+asyncpg://aegis_app:x@db.internal:5432/aegis",
    )
    with pytest.raises(UnsafeConfiguration):
        create_app(settings, engine=app_engine, scenarios=registry)
