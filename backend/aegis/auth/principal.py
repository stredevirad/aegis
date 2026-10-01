"""The authenticated caller of a request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from aegis.audit import Actor
from aegis.auth.rbac import Permission


@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "api_key"]
    org_id: UUID
    permissions: frozenset[Permission]
    user_id: UUID | None = None
    role: str | None = None
    session_id: UUID | None = None
    api_key_id: UUID | None = None
    api_key_prefix: str | None = None

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions

    @property
    def actor(self) -> Actor:
        if self.kind == "user":
            return Actor("user", str(self.user_id))
        return Actor("api_key", f"{self.api_key_id} ({self.api_key_prefix})")

    @property
    def label(self) -> str:
        """Short identifier for ``requested_by`` / event actor fields (no PII)."""
        if self.kind == "user":
            return f"user:{self.user_id}"
        return f"api_key:{self.api_key_prefix}"
