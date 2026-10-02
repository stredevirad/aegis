"""Client for the flag-service remediation target (the one real executor, ADR-014).

Talks to an operator-registered ``flag_service`` connector through the egress client:

    GET  /flags/{key}   → {"key", "value", "version", ...}
    POST /flags/{key}   ← {"value", "actor", "if_version"}   (compare-and-set at the target)

The flag key comes from the organization's catalog entry (validated pattern), never from a
model or a request body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aegis.integrations.egress import Credential, EgressClient, EgressDenied, EgressError
from aegis.integrations.registry import Connector

FLAG_KEY = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class FlagError(Exception):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class FlagState:
    value: str | None
    version: int

    def as_dict(self) -> dict[str, object]:
        return {"value": self.value, "version": self.version}


@dataclass
class FlagClient:
    egress: EgressClient
    connector: Connector
    credential: Credential

    def _path(self, key: str) -> str:
        if not FLAG_KEY.match(key):
            raise FlagError("invalid_key", "flag key not allowed")
        return f"/flags/{key}"

    async def get(self, key: str) -> FlagState:
        try:
            body = (
                await self.egress.request(
                    self.connector, "GET", self._path(key), credential=self.credential
                )
            ).json()
        except EgressDenied as exc:
            raise FlagError("denied", exc.reason) from None
        except EgressError as exc:
            raise FlagError(exc.category, str(exc)) from None
        try:
            value = body.get("value")
            return FlagState(
                None if value is None else str(value)[:100], int(body.get("version", 0))
            )
        except (AttributeError, TypeError, ValueError):
            raise FlagError("invalid_response", "unexpected flag-service response") from None

    async def set(self, key: str, value: str, actor: str, if_version: int) -> FlagState:
        """Compare-and-set; never retried (a non-idempotent write)."""
        try:
            body = (
                await self.egress.request(
                    self.connector,
                    "POST",
                    self._path(key),
                    credential=self.credential,
                    retries=0,
                    json_body={"value": value, "actor": actor[:100], "if_version": if_version},
                )
            ).json()
        except EgressDenied as exc:
            raise FlagError("denied", exc.reason) from None
        except EgressError as exc:
            category = "conflict" if exc.status == 409 else exc.category
            raise FlagError(category, str(exc)) from None
        try:
            return FlagState(str(body.get("value"))[:100], int(body["version"]))
        except (AttributeError, KeyError, TypeError, ValueError):
            raise FlagError("invalid_response", "unexpected flag-service response") from None
