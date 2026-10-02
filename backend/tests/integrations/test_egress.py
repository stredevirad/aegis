"""SSRF defences of the egress client and the connector registry (ADR-016, Council C).

Uses a local HTTP server on 127.0.0.1 (development profile, ``allow_loopback``) for the
network-level tests, and a fake resolver for DNS answers that cannot be produced locally
(metadata addresses, mixed answers, rebinding to infrastructure).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest

from aegis.config import Profile, Settings
from aegis.integrations import registry as reg
from aegis.integrations.egress import (
    Credential,
    EgressClient,
    EgressDenied,
    EgressError,
    check_path,
    classify,
)
from aegis.integrations.registry import Connector, ConnectorKind, RegistryError

DEV = Settings(
    profile=Profile.DEVELOPMENT,
    database_url="postgresql+asyncpg://aegis_app:x@db.internal:5432/aegis",
)
PROD = Settings(
    profile=Profile.PRODUCTION,
    database_url="postgresql+asyncpg://aegis_app:x@db.internal:5432/aegis",
)


def connector(url: str, kind=ConnectorKind.PROMETHEUS, **kw) -> Connector:
    return Connector(id=kw.pop("id", "c1"), kind=kind, url=url, orgs=kw.pop("orgs", ["acme"]), **kw)


def fake_resolver(table: dict[str, list[str]]):
    async def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise OSError("NXDOMAIN")
        return table[host]

    return resolve


# --- address classification ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("169.254.169.254", "deny"),
        ("169.254.170.2", "deny"),  # ECS task metadata
        ("100.100.100.200", "deny"),
        ("192.0.0.192", "deny"),
        ("fd00:ec2::254", "deny"),
        ("::ffff:169.254.169.254", "deny"),  # IPv4-mapped IPv6
        ("64:ff9b::a9fe:a9fe", "deny"),  # NAT64 of 169.254.169.254
        ("2002:a9fe:a9fe::1", "deny"),  # 6to4 of 169.254.169.254
        ("0.0.0.0", "deny"),
        ("::", "deny"),
        ("224.0.0.1", "deny"),
        ("255.255.255.255", "deny"),
        ("fe80::1", "deny"),
        ("not-an-ip", "deny"),
        ("127.0.0.1", "loopback"),
        ("127.8.9.10", "loopback"),
        ("::1", "loopback"),
        ("::ffff:127.0.0.1", "loopback"),
        ("10.1.2.3", "private"),
        ("172.20.0.5", "private"),
        ("192.168.1.1", "private"),
        ("100.64.0.1", "private"),
        ("fd12:3456::1", "private"),
        ("::ffff:10.0.0.1", "private"),
        ("::127.0.0.1", "deny"),  # deprecated IPv4-compatible form
        ("8.8.8.8", "public"),
        ("2606:4700::1111", "public"),
    ],
)
def test_address_classification(address, expected):
    assert classify(address) == expected


# --- paths -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/query_range/../../admin",
        "/api/v1/%2e%2e/admin",
        "/api/v1/query_range%2F..%2F",
        "//evil.example/api/v1/query",
        "/api/v1/query_range?query=up",
        "/api/v1/query_range#x",
        "/api/v1/query_range\\..\\",
        "api/v1/query",
        "/api/v1/admin/tsdb/delete_series",  # real Prometheus admin API
        "/api/v1/query_range\n",
        "",
    ],
)
def test_paths_outside_the_allowlist_or_malformed_are_denied(path):
    with pytest.raises(EgressDenied):
        check_path(ConnectorKind.PROMETHEUS, path)


def test_ollama_admin_endpoints_are_never_allowed():
    for path in (
        "/api/pull",
        "/api/push",
        "/api/delete",
        "/api/create",
        "/api/copy",
        "/api/blobs/sha256:x",
    ):
        with pytest.raises(EgressDenied):
            check_path(ConnectorKind.LLM, path)
    assert check_path(ConnectorKind.LLM, "/api/chat") == "/api/chat"


def test_jaeger_trace_ids_must_be_hex():
    assert check_path(ConnectorKind.JAEGER, "/api/traces/4bf92f3577b34da6a3ce929d0e0e4736")
    for bad in ("/api/traces/../services", "/api/traces/x?limit=9999", "/api/traces/abc/def"):
        with pytest.raises(EgressDenied):
            check_path(ConnectorKind.JAEGER, bad)


# --- DNS / address policy -------------------------------------------------------------------------------


async def test_metadata_and_mixed_answers_are_denied_even_with_allow_private():
    c = EgressClient(
        DEV,
        resolver=fake_resolver(
            {
                "meta.example": ["169.254.169.254"],
                "mixed.example": ["10.0.0.5", "169.254.169.254"],
                "mapped.example": ["::ffff:169.254.169.254"],
            }
        ),
    )
    for host in ("meta.example", "mixed.example", "mapped.example"):
        with pytest.raises(EgressDenied):
            await c.pin(connector(f"http://{host}:9090", allow_private=True))


async def test_loopback_and_private_policy():
    resolver = fake_resolver(
        {"lo.example": ["127.0.0.1"], "priv.example": ["10.0.0.7"], "pub.example": ["8.8.8.8"]}
    )
    dev = EgressClient(DEV, resolver=resolver)
    with pytest.raises(EgressDenied):
        await dev.pin(connector("http://lo.example:9090"))
    assert await dev.pin(connector("http://lo.example:9090", allow_loopback=True)) == "127.0.0.1"
    with pytest.raises(EgressDenied):
        await dev.pin(connector("http://priv.example:9090"))
    assert await dev.pin(connector("http://priv.example:9090", allow_private=True)) == "10.0.0.7"
    assert await dev.pin(connector("http://pub.example:9090")) == "8.8.8.8"
    # allow_loopback is ignored outside development
    prod = EgressClient(PROD, resolver=resolver)
    with pytest.raises(EgressDenied):
        await prod.pin(connector("http://lo.example:9090", allow_loopback=True))


async def test_aegis_infrastructure_is_denied_by_name_and_by_address():
    resolver = fake_resolver(
        {"db.internal": ["10.9.9.9"], "sneaky.example": ["10.9.9.9"], "api.example": ["10.9.9.10"]}
    )
    settings = Settings(
        profile=Profile.DEVELOPMENT,
        database_url="postgresql+asyncpg://aegis_app:x@db.internal:5432/aegis",
        egress_deny=["ollama.internal:11434"],
    )
    c = EgressClient(settings, resolver=resolver)
    with pytest.raises(EgressDenied):
        await c.pin(connector("http://db.internal:5432", allow_private=True))
    with pytest.raises(EgressDenied):  # a tenant-chosen name that resolves to the database
        await c.pin(connector("http://sneaky.example:5432", allow_private=True))
    # same address, different port: not the database
    assert await c.pin(connector("http://sneaky.example:9090", allow_private=True)) == "10.9.9.9"
    with pytest.raises(EgressDenied):
        await c.pin(
            connector("http://ollama.internal:11434", allow_private=True, kind=ConnectorKind.LLM)
        )


async def test_dns_failure_is_an_error_not_a_policy_pass():
    c = EgressClient(DEV, resolver=fake_resolver({}))
    with pytest.raises(EgressError) as e:
        await c.pin(connector("http://nowhere.example:9090"))
    assert e.value.category == "dns"


# --- real network behaviour against a local server --------------------------------------------------------


@dataclass
class Server:
    port: int = 0
    requests: list[dict] = field(default_factory=list)
    script: list[tuple] = field(default_factory=list)  # (status, headers, body, delay)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        headers = {k.lower(): v for k, _, v in (ln.partition(": ") for ln in lines[1:] if ln)}
        length = int(headers.get("content-length", "0"))
        body = await reader.readexactly(length) if length else b""
        self.requests.append({"line": lines[0], "headers": headers, "body": body})
        status, extra, payload, delay = (
            self.script.pop(0) if self.script else (200, {}, b'{"ok": true}', 0)
        )
        if delay:
            await asyncio.sleep(delay)
        hdrs = {"Content-Type": "application/json", "Content-Length": str(len(payload)), **extra}
        writer.write(
            f"HTTP/1.1 {status} X\r\n".encode()
            + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()).encode()
            + b"\r\n"
            + payload
        )
        await writer.drain()
        writer.close()


@asynccontextmanager
async def local_server():
    s = Server()
    server = await asyncio.start_server(s.handle, "127.0.0.1", 0)
    s.port = server.sockets[0].getsockname()[1]
    async with server:
        yield s


async def test_pinned_connection_keeps_host_header_and_credentials_out_of_urls():
    async with local_server() as s:
        c = EgressClient(DEV, resolver=fake_resolver({"prom.test": ["127.0.0.1"]}))
        conn = connector(f"http://prom.test:{s.port}", allow_loopback=True)
        r = await c.request(
            conn,
            "GET",
            "/api/v1/query",
            params={"query": "up"},
            credential=Credential("bearer", token="s3cret-token"),
        )
        await c.aclose()
    assert r.status == 200 and r.json() == {"ok": True}
    req = s.requests[0]
    assert req["headers"]["host"] == f"prom.test:{s.port}"
    assert req["headers"]["authorization"] == "Bearer s3cret-token"
    assert "s3cret" not in req["line"] and "s3cret" not in r.provenance
    assert r.provenance == "GET c1 /api/v1/query?query=up"


async def test_redirects_are_never_followed():
    async with local_server() as s:
        s.script.append((302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b"", 0))
        c = EgressClient(DEV, resolver=fake_resolver({"prom.test": ["127.0.0.1"]}))
        with pytest.raises(EgressError) as e:
            await c.request(
                connector(f"http://prom.test:{s.port}", allow_loopback=True), "GET", "/api/v1/query"
            )
        await c.aclose()
    assert e.value.category == "redirect" and len(s.requests) == 1


async def test_oversized_responses_are_cut_off():
    async with local_server() as s:
        s.script.append((200, {}, b"x" * 5000, 0))
        c = EgressClient(DEV, resolver=fake_resolver({"prom.test": ["127.0.0.1"]}))
        conn = connector(f"http://prom.test:{s.port}", allow_loopback=True, max_response_bytes=2048)
        with pytest.raises(EgressError) as e:
            await c.request(conn, "GET", "/api/v1/query")
        await c.aclose()
    assert e.value.category == "too_large"


async def test_timeouts_and_bounded_retries():
    async with local_server() as s:
        s.script += [(503, {}, b"{}", 0), (200, {}, b'{"ok": 1}', 0)]
        c = EgressClient(DEV, resolver=fake_resolver({"prom.test": ["127.0.0.1"]}))
        conn = connector(f"http://prom.test:{s.port}", allow_loopback=True, timeout_seconds=1)
        assert (await c.request(conn, "GET", "/api/v1/query")).json() == {"ok": 1}
        assert len(s.requests) == 2
        s.script += [(503, {}, b"{}", 0)]
        with pytest.raises(EgressError):  # POST (non-idempotent) is never retried
            await c.request(
                connector(
                    f"http://prom.test:{s.port}", kind=ConnectorKind.LLM, allow_loopback=True
                ),
                "POST",
                "/api/chat",
                json_body={"x": 1},
            )
        assert len(s.requests) == 3
        s.script += [(200, {}, b"{}", 3)]
        with pytest.raises(EgressError) as e:
            await c.request(conn, "GET", "/api/v1/query", retries=0)
        await c.aclose()
    assert e.value.category == "timeout"


async def test_forbidden_methods_and_headers():
    c = EgressClient(DEV, resolver=fake_resolver({"prom.test": ["127.0.0.1"]}))
    conn = connector("http://prom.test:9", allow_loopback=True)
    with pytest.raises(EgressDenied):
        await c.request(conn, "DELETE", "/api/v1/query")
    with pytest.raises(EgressDenied):
        await c.request(conn, "GET", "/api/v1/query", headers={"Host": "evil"})
    with pytest.raises(EgressDenied):
        await c.request(
            conn,
            "GET",
            "/api/v1/query",
            credential=Credential("header", header_name="Cookie", header_value="x"),
        )


# --- registry -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://prom:21",
        "http://user:pw@prom:9090",
        "http://prom:9090/?x=1",
        "http://prom:9090/#f",
        "http://*.internal:9090",
        "http://10.0.0.%2A:9090",
        "http://prom%00.internal:9090",
        "http://prom:99999",
        "http://prom:9090/a/../b",
        "file:///etc/passwd",
        "gopher://prom:70",
    ],
)
def test_registry_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        connector(url)


def test_registry_validation_rules(tmp_path):
    db_collision = connector("http://db.internal:5432", id="db", allow_private=True)
    http_prod = connector("http://prom:9090", id="plain")
    loop_prod = connector("https://prom:9090", id="lo", allow_loopback=True)
    assert any("infrastructure" in p for p in reg.validate([db_collision], DEV))
    problems = reg.validate([http_prod, loop_prod, connector("https://x:1", id="plain")], PROD)
    assert any("plain http" in p for p in problems)
    assert any("allow_loopback" in p for p in problems)
    assert any("duplicate" in p for p in problems)
    assert reg.validate([connector("http://prom:9090", id="ok", insecure_http=True)], PROD) == []

    f = tmp_path / "connectors.yaml"
    f.write_text(
        "connectors:\n  - id: prom-a\n    kind: prometheus\n    url: http://prom:9090\n    orgs: [acme]\n"
    )
    r = reg.load(DEV.model_copy(update={"connectors_file": f}))
    assert (
        len(r) == 1 and [c.id for c in r.for_org("acme")] == ["prom-a"] and r.for_org("other") == []
    )
    with pytest.raises(RegistryError):
        r.resolve_for_org("prom-a", "other")  # tenant B cannot use tenant A's connector
    f.write_text(
        "connectors:\n  - id: bad\n    kind: prometheus\n    url: http://u:p@prom\n    orgs: [acme]\n"
    )
    with pytest.raises(RegistryError):
        reg.load(DEV.model_copy(update={"connectors_file": f}))


def test_public_view_hides_options():
    c = connector("http://prom:9090", options={"secret_query": "x"})
    assert c.public_view() == {
        "id": "c1",
        "kind": "prometheus",
        "host": "prom",
        "port": 9090,
        "scheme": "http",
    }
