"""Operator-owned connector registry (ADR-016, security model C8).

Tenants never type URLs. The deployment operator lists every outbound destination in a YAML
file (``AEGIS_CONNECTORS_FILE``); each connector names one exact endpoint (scheme, host, port,
optional base path) and the organizations allowed to use it. Organization admins create
integrations that *reference a connector ID* and supply credentials; the model, an alert
payload or an evidence field can never choose a destination.

Validation at startup (any violation refuses to start):

* ``http``/``https`` only; no credentials, query or fragment in the URL; the host is a single
  DNS name or IP literal — wildcards and CIDR ranges are rejected;
* every connector is bound to at least one organization;
* a connector may not point at Aegis's own infrastructure (the database, the API's public
  address, or anything in ``AEGIS_EGRESS_DENY``);
* outside development, plain ``http`` requires an explicit ``insecure_http: true`` and
  ``allow_loopback`` is ignored.

Example::

    connectors:
      - id: prom-main
        kind: prometheus
        url: http://prometheus.monitoring:9090
        orgs: [acme]
        allow_private: true        # the resolved address may be RFC 1918 / ULA
        insecure_http: true        # in-cluster plain HTTP, acknowledged by the operator
        options:
          request_rate: 'sum by (service) (rate(http_server_request_duration_seconds_count{service=~"$services"}[1m]))'
"""

from __future__ import annotations

import ipaddress
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aegis.config import Settings

_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
)


class ConnectorKind(StrEnum):
    PROMETHEUS = "prometheus"
    LOKI = "loki"
    OPENSEARCH = "opensearch"
    JAEGER = "jaeger"
    TEMPO = "tempo"
    KUBERNETES = "kubernetes"
    LLM = "llm"
    WEBHOOK = "webhook"
    SLACK = "slack"
    PAGERDUTY = "pagerduty"
    FLAG_SERVICE = "flag_service"
    MCP = "mcp"


TELEMETRY_KINDS = frozenset(
    {
        ConnectorKind.PROMETHEUS,
        ConnectorKind.LOKI,
        ConnectorKind.OPENSEARCH,
        ConnectorKind.JAEGER,
        ConnectorKind.TEMPO,
        ConnectorKind.KUBERNETES,
    }
)


class RegistryError(ValueError):
    pass


class Connector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]
    kind: ConnectorKind
    url: Annotated[str, Field(max_length=500)]
    orgs: Annotated[
        list[Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]], Field(min_length=1)
    ]
    ca_file: Path | None = None
    allow_private: bool = False
    allow_loopback: bool = False
    insecure_http: bool = False
    timeout_seconds: Annotated[float, Field(ge=1, le=120)] = 10.0
    max_response_bytes: Annotated[int, Field(ge=1024, le=64 * 1024 * 1024)] = 10 * 1024 * 1024
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https"):
            raise ValueError("only http and https connectors are allowed")
        if parts.username or parts.password or "@" in parts.netloc:
            raise ValueError("credentials must not appear in connector URLs")
        if parts.query or parts.fragment:
            raise ValueError("connector URLs must not have a query or fragment")
        host = parts.hostname or ""
        if not host or "*" in host or "/" in host:
            raise ValueError(
                "connector host must be a single DNS name or IP address (no wildcards or ranges)"
            )
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _HOSTNAME.match(host):
                raise ValueError(f"invalid connector host {host!r}") from None
        try:
            _ = parts.port
        except ValueError:
            raise ValueError("invalid connector port") from None
        if ".." in parts.path or "//" in parts.path:
            raise ValueError("connector base path must be normalised")
        return v.rstrip("/")

    @model_validator(mode="after")
    def _ca(self) -> Connector:
        if self.ca_file is not None and not self.url.startswith("https://"):
            raise ValueError("ca_file only applies to https connectors")
        return self

    @property
    def scheme(self) -> str:
        return urlsplit(self.url).scheme

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").lower()

    @property
    def port(self) -> int:
        p = urlsplit(self.url).port
        return p if p is not None else (443 if self.scheme == "https" else 80)

    @property
    def base_path(self) -> str:
        return urlsplit(self.url).path.rstrip("/")

    def allowed_for(self, org_slug: str) -> bool:
        return org_slug in self.orgs

    def public_view(self) -> dict[str, Any]:
        """What tenants may see: never options or CA paths."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "host": self.host,
            "port": self.port,
            "scheme": self.scheme,
        }


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connectors: list[Connector] = Field(default_factory=list)


def _host_port(url: str, default_port: int) -> tuple[str, int] | None:
    try:
        parts = urlsplit(url)
        return (parts.hostname or "").lower(), parts.port or default_port
    except ValueError:
        return None


def infrastructure_endpoints(settings: Settings) -> set[tuple[str, int]]:
    """(host, port) pairs of Aegis itself that no connector may target."""
    out: set[tuple[str, int]] = set()
    for url, default in (
        (settings.database_url.get_secret_value(), 5432),
        (settings.public_base_url, 443),
    ):
        hp = _host_port(url.replace("postgresql+asyncpg://", "postgresql://"), default)
        if hp and hp[0]:
            out.add(hp)
    for entry in settings.egress_deny:
        host, _, port = entry.rpartition(":")
        if host and port.isdigit():
            out.add((host.lower().strip("[]"), int(port)))
    return out


class Registry:
    def __init__(self, connectors: list[Connector]) -> None:
        self._by_id = {c.id: c for c in connectors}

    def __len__(self) -> int:
        return len(self._by_id)

    def for_all(self) -> list[Connector]:
        return sorted(self._by_id.values(), key=lambda c: c.id)

    def get(self, connector_id: str) -> Connector | None:
        return self._by_id.get(connector_id)

    def for_org(
        self, org_slug: str, kinds: frozenset[ConnectorKind] | None = None
    ) -> list[Connector]:
        return sorted(
            (
                c
                for c in self._by_id.values()
                if c.allowed_for(org_slug) and (kinds is None or c.kind in kinds)
            ),
            key=lambda c: c.id,
        )

    def resolve_for_org(self, connector_id: str, org_slug: str) -> Connector:
        c = self._by_id.get(connector_id)
        if c is None or not c.allowed_for(org_slug):
            raise RegistryError("connector not available to this organization")
        return c


def validate(connectors: list[Connector], settings: Settings) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    infra = infrastructure_endpoints(settings)
    for c in connectors:
        if c.id in seen:
            problems.append(f"duplicate connector id {c.id}")
        seen.add(c.id)
        if (c.host, c.port) in infra:
            problems.append(
                f"connector {c.id} targets Aegis's own infrastructure ({c.host}:{c.port})"
            )
        if not settings.dev_like:
            if c.scheme == "http" and not c.insecure_http:
                problems.append(
                    f"connector {c.id} uses plain http; set insecure_http: true to acknowledge"
                )
            if c.allow_loopback:
                problems.append(
                    f"connector {c.id}: allow_loopback is only permitted in development"
                )
        if c.ca_file is not None and not c.ca_file.is_file():
            problems.append(f"connector {c.id}: ca_file {c.ca_file} does not exist")
    return problems


def load(settings: Settings) -> Registry:
    path = settings.connectors_file
    if path is None:
        return Registry([])
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        parsed = _File.model_validate(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise RegistryError(
            f"cannot read connector registry {path}: {type(exc).__name__}"
        ) from None
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:20]
        )
        raise RegistryError(f"invalid connector registry {path}: {details}") from None
    problems = validate(parsed.connectors, settings)
    if problems:
        raise RegistryError("invalid connector registry: " + "; ".join(problems))
    return Registry(parsed.connectors)
