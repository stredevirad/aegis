"""The single outbound HTTP path for every integration (ADR-016, security model C8).

Every request names a registered :class:`Connector` and a *relative* path; there is no API
that accepts a URL. Before connecting, the client:

1. checks the path against the connector kind's **path allowlist** (so even a buggy adapter
   cannot reach, say, Ollama's ``/api/pull`` or an arbitrary endpoint on the host) and
   rejects traversal, encoded separators and control characters;
2. resolves the host **once** and checks **every** returned address. Always denied: link-local
   (incl. cloud metadata 169.254.169.254 / fd00:ec2::254), Alibaba/Oracle metadata,
   unspecified, multicast, broadcast and reserved ranges; IPv4 embedded in IPv6 (mapped,
   NAT64, 6to4) is unwrapped and checked as IPv4. Loopback only in development with
   ``allow_loopback``; private ranges (RFC 1918, ULA, CGNAT) only with ``allow_private``.
   If *any* answer is denied the request is refused (a mixed answer is treated as hostile);
3. refuses Aegis's own infrastructure (database, API, configured deny list) by host and by
   resolved address;
4. connects to the **pinned** address while sending the original ``Host`` header and TLS SNI,
   so certificates are verified against the registered hostname and DNS rebinding between
   check and connect is impossible;
5. never follows redirects, caps the response size while streaming, applies connect/read/total
   timeouts, retries only idempotent requests on transient failures (bounded, jittered);
6. puts credentials only in headers (never URLs, never error messages, never provenance).

Allowed claim: "Aegis connects only to operator-registered endpoints and never to its own
infrastructure or metadata addresses" — tested in ``tests/integrations/test_egress.py``.
Forbidden claim: "SSRF-proof".
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import random
import re
import socket
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlencode

import httpx

from aegis.config import Settings
from aegis.integrations.registry import Connector, ConnectorKind, infrastructure_endpoints

IP = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

_ALWAYS_DENY = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "169.254.0.0/16",  # link-local, incl. 169.254.169.254 (AWS/GCP/Azure metadata)
        "100.100.100.200/32",  # Alibaba Cloud metadata
        "192.0.0.0/24",  # IETF protocol assignments (incl. 192.0.0.192 Oracle metadata)
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",  # documentation ranges
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, incl. broadcast
        "::/128",
        "fe80::/10",  # IPv6 link-local
        "fd00:ec2::254/128",  # AWS IPv6 metadata
        "ff00::/8",  # multicast
        "2001:db8::/32",  # documentation
    )
]
_LOOPBACK = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]
_PRIVATE = [
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "198.18.0.0/15",
        "fc00::/7",
    )
]
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")

# Path allowlists per connector kind (regular expressions over the path *relative to the
# connector's base path*). Adapters may only call what is listed here.
_HEX = r"[0-9a-fA-F]{1,64}"
PATH_ALLOWLIST: dict[ConnectorKind, list[str]] = {
    ConnectorKind.PROMETHEUS: [
        r"/api/v1/query_range",
        r"/api/v1/query",
        r"/api/v1/status/buildinfo",
        r"/-/ready",
    ],
    ConnectorKind.LOKI: [
        r"/loki/api/v1/query_range",
        r"/loki/api/v1/query",
        r"/ready",
        r"/loki/api/v1/status/buildinfo",
    ],
    ConnectorKind.JAEGER: [r"/api/traces", rf"/api/traces/{_HEX}", r"/api/services"],
    ConnectorKind.TEMPO: [
        r"/api/search",
        rf"/api/traces/{_HEX}",
        r"/ready",
        r"/api/status/buildinfo",
    ],
    ConnectorKind.OPENSEARCH: [r"/[a-z0-9][a-z0-9._*-]{0,99}/_search", r"/_cluster/health", r"/"],
    ConnectorKind.KUBERNETES: [
        r"/api/v1/namespaces/[a-z0-9-]{1,63}/events",
        r"/apis/apps/v1/namespaces/[a-z0-9-]{1,63}/(deployments|replicasets)",
        r"/api/v1/namespaces/[a-z0-9-]{1,63}/pods",
        r"/version",
    ],
    # LLM: Ollama chat/tags/version/show, OpenAI-compatible chat/models, Anthropic messages.
    # Never Ollama's pull/push/delete/create/copy.
    ConnectorKind.LLM: [
        r"/api/chat",
        r"/api/tags",
        r"/api/version",
        r"/api/show",
        r"/v1/chat/completions",
        r"/v1/models",
        r"/v1/messages",
    ],
    ConnectorKind.WEBHOOK: [r"", r"/"],
    ConnectorKind.SLACK: [r"", r"/"],
    ConnectorKind.PAGERDUTY: [r"/v2/enqueue"],
    ConnectorKind.FLAG_SERVICE: [r"/flags", r"/flags/[A-Za-z0-9._-]{1,100}", r"/healthz"],
    ConnectorKind.MCP: [r"", r"/", r"/mcp"],
}
_COMPILED = {k: [re.compile(p + r"\Z") for p in v] for k, v in PATH_ALLOWLIST.items()}
_FORBIDDEN_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "cookie",
    "te",
    "upgrade",
    "proxy-authorization",
    "x-forwarded-for",
    "x-forwarded-host",
    "forwarded",
}
_HEADER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,60}$")
RETRYABLE_STATUS = {429, 502, 503, 504}


class EgressDenied(Exception):
    """The policy refused the request before any connection was made."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EgressError(Exception):
    """The request was attempted and failed. ``category`` is stable; ``str()`` is safe to log."""

    def __init__(self, category: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.status = status


@dataclass(frozen=True)
class Credential:
    kind: str  # none | bearer | basic | header
    token: str | None = None
    username: str | None = None
    password: str | None = None
    header_name: str | None = None
    header_value: str | None = None

    def headers(self) -> dict[str, str]:
        if self.kind == "bearer" and self.token:
            return {"Authorization": f"Bearer {self.token}"}
        if self.kind == "basic" and self.username is not None and self.password is not None:
            raw = f"{self.username}:{self.password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        if self.kind == "header" and self.header_name and self.header_value is not None:
            if (
                not _HEADER_NAME.match(self.header_name)
                or self.header_name.lower() in _FORBIDDEN_HEADERS
            ):
                raise EgressDenied("credential header name not allowed")
            return {self.header_name: self.header_value}
        return {}


NO_CREDENTIAL = Credential("none")


@dataclass(frozen=True)
class EgressResponse:
    status: int
    headers: Mapping[str, str]
    content: bytes
    provenance: str  # credential-free description: "GET prom-main /api/v1/query_range?..."

    def json(self) -> Any:
        import json

        try:
            return json.loads(self.content)
        except ValueError:
            raise EgressError("invalid_response", "response is not valid JSON") from None


def _unwrap(ip: IP) -> IP:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip in _SIX_TO_FOUR:
            return ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    return ip


def classify(address: str) -> str:
    """'deny' | 'loopback' | 'private' | 'public' for one resolved address."""
    try:
        ip = _unwrap(ipaddress.ip_address(address.split("%", 1)[0]))
    except ValueError:
        return "deny"
    if any(ip in n for n in _ALWAYS_DENY if n.version == ip.version):
        return "deny"
    # loopback before the generic reserved check: Python counts ::/8 (incl. ::1) as reserved
    if any(ip in n for n in _LOOPBACK if n.version == ip.version):
        return "loopback"
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "deny"
    if any(ip in n for n in _PRIVATE if n.version == ip.version) or ip.is_private:
        return "private"
    return "public"


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos}, key=lambda a: (":" in a, a))


def check_path(kind: ConnectorKind, path: str) -> str:
    if not path or len(path) > 1024:
        raise EgressDenied("invalid path")
    decoded = unquote(path)
    if (
        not path.startswith("/")
        or "//" in path
        or "\\" in decoded
        or any(seg in ("..", ".") for seg in decoded.split("/"))
        or "?" in path
        or "#" in path
        or any(ord(c) < 0x21 or ord(c) == 0x7F for c in decoded)
        or decoded != path  # no percent-encoding at all in paths
    ):
        raise EgressDenied("path rejected")
    if not any(p.match(path) for p in _COMPILED.get(kind, [])):
        raise EgressDenied(f"path not allowed for {kind.value} connectors")
    return path


@dataclass
class EgressClient:
    settings: Settings
    resolver: Resolver = system_resolver
    _clients: dict[str, httpx.AsyncClient] = field(default_factory=dict)
    _infra_ips: set[tuple[str, int]] | None = None

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()

    def _client(self, connector: Connector) -> httpx.AsyncClient:
        # one pool per connector: TLS sessions are never shared across registered endpoints
        c = self._clients.get(connector.id)
        if c is None:
            verify: ssl.SSLContext | bool = True
            if connector.scheme == "https":
                verify = ssl.create_default_context(
                    cafile=str(connector.ca_file) if connector.ca_file else None
                )
            c = httpx.AsyncClient(
                verify=verify,
                follow_redirects=False,
                trust_env=False,  # no proxy environment variables: the policy decides
                timeout=httpx.Timeout(
                    connector.timeout_seconds, connect=min(5.0, connector.timeout_seconds)
                ),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
            )
            self._clients[connector.id] = c
        return c

    async def _infrastructure(self) -> set[tuple[str, int]]:
        if self._infra_ips is None:
            resolved: set[tuple[str, int]] = set()
            for host, port in infrastructure_endpoints(self.settings):
                resolved.add((host, port))
                try:
                    for a in await self.resolver(host, port):
                        resolved.add((str(_unwrap(ipaddress.ip_address(a.split("%", 1)[0]))), port))
                except (OSError, ValueError):
                    continue
            self._infra_ips = resolved
        return self._infra_ips

    async def pin(self, connector: Connector) -> str:
        """Resolve and vet the connector's host; return the single address to connect to."""
        infra = await self._infrastructure()
        if (connector.host, connector.port) in infra:
            raise EgressDenied("destination is Aegis infrastructure")
        try:
            addresses = await self.resolver(connector.host, connector.port)
        except OSError:
            raise EgressError("dns", f"cannot resolve {connector.host}") from None
        if not addresses:
            raise EgressError("dns", f"no addresses for {connector.host}")
        for a in addresses:
            kind = classify(a)
            if kind == "deny":
                raise EgressDenied(f"{connector.host} resolves to a forbidden address")
            if kind == "loopback" and not (connector.allow_loopback and self.settings.dev_like):
                raise EgressDenied(f"{connector.host} resolves to loopback")
            if kind == "private" and not connector.allow_private:
                raise EgressDenied(
                    f"{connector.host} resolves to a private address (allow_private is off)"
                )
            unwrapped = str(_unwrap(ipaddress.ip_address(a.split("%", 1)[0])))
            if (unwrapped, connector.port) in infra:
                raise EgressDenied("destination resolves to Aegis infrastructure")
        return addresses[0]

    async def request(
        self,
        connector: Connector,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | float] | None = None,
        json_body: Any = None,
        credential: Credential = NO_CREDENTIAL,
        headers: Mapping[str, str] | None = None,
        retries: int = 1,
        max_bytes: int | None = None,
    ) -> EgressResponse:
        method = method.upper()
        if method not in ("GET", "POST", "HEAD"):
            raise EgressDenied("method not allowed")
        check_path(connector.kind, path)
        extra = dict(headers or {})
        for name in extra:
            if not _HEADER_NAME.match(name) or name.lower() in _FORBIDDEN_HEADERS:
                raise EgressDenied("header not allowed")
        address = await self.pin(connector)
        host_literal = f"[{address}]" if ":" in address else address
        default_port = 443 if connector.scheme == "https" else 80
        host_header = (
            connector.host
            if connector.port == default_port
            else f"{connector.host}:{connector.port}"
        )
        url = f"{connector.scheme}://{host_literal}:{connector.port}{connector.base_path}{path}"
        req_headers = {
            "Host": host_header,
            "User-Agent": "aegis",
            "Accept": "application/json",
            **extra,
        }
        req_headers.update(credential.headers())
        query = {k: str(v) for k, v in (params or {}).items()}
        provenance = f"{method} {connector.id} {path}" + (
            f"?{urlencode(sorted(query.items()))}" if query else ""
        )
        limit = min(max_bytes or connector.max_response_bytes, connector.max_response_bytes)
        attempts = 1 + (retries if method in ("GET", "HEAD") else 0)
        last: EgressError | None = None
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(min(2.0, 0.25 * 2**attempt) * random.uniform(0.5, 1.5))  # noqa: S311
            try:
                return await self._once(
                    connector, method, url, query, req_headers, json_body, limit, provenance
                )
            except EgressError as exc:
                last = exc
                if (
                    exc.category not in ("connect", "timeout")
                    and exc.status not in RETRYABLE_STATUS
                ):
                    raise
        assert last is not None
        raise last

    async def _once(
        self,
        connector: Connector,
        method: str,
        url: str,
        query: dict[str, str],
        headers: dict[str, str],
        json_body: Any,
        limit: int,
        provenance: str,
    ) -> EgressResponse:
        client = self._client(connector)
        extensions = {"sni_hostname": connector.host} if connector.scheme == "https" else {}
        try:
            async with asyncio.timeout(connector.timeout_seconds * 1.5):
                req = client.build_request(
                    method,
                    url,
                    params=query or None,
                    headers=headers,
                    json=json_body,
                    extensions=extensions,
                )
                resp = await client.send(req, stream=True)
                try:
                    if 300 <= resp.status_code < 400:
                        raise EgressError(
                            "redirect",
                            f"redirect refused (HTTP {resp.status_code})",
                            resp.status_code,
                        )
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > limit:
                        raise EgressError("too_large", f"response exceeds {limit} bytes")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise EgressError("too_large", f"response exceeds {limit} bytes")
                        chunks.append(chunk)
                finally:
                    await resp.aclose()
        except TimeoutError:
            raise EgressError("timeout", f"{connector.id} timed out") from None
        except httpx.TimeoutException:
            raise EgressError("timeout", f"{connector.id} timed out") from None
        except httpx.ConnectError as exc:
            tls = isinstance(exc.__cause__, ssl.SSLError) or "SSL" in type(exc.__cause__).__name__
            raise EgressError(
                "tls" if tls else "connect", f"cannot connect to {connector.id}"
            ) from None
        except httpx.HTTPError as exc:
            raise EgressError("http", f"{connector.id}: {type(exc).__name__}") from None
        body = b"".join(chunks)
        if resp.status_code >= 400:
            raise EgressError(
                "http_status", f"{connector.id} returned HTTP {resp.status_code}", resp.status_code
            )
        return EgressResponse(resp.status_code, dict(resp.headers), body, provenance[:500])
