"""Recording audit events.

``record`` inserts into ``audit_events`` on the caller's connection, i.e. inside the same
tenant transaction as the action it describes: the action and its audit record commit or roll
back together. The database trigger assigns the sequence number, timestamp and hash chain;
the application role can only INSERT and SELECT.

What the chain protects against: accidental or application-level modification (the
application role cannot UPDATE or DELETE, and any edit by a more privileged role breaks
``audit_verify()``). What it does not protect against: the database owner or operator, who
can rewrite the chain consistently (documented trust boundary, docs/security-model.md §2).

Security events that happen before an organization is known (unknown API key, failed login)
cannot be attributed to a tenant and are written to the structured security log instead
(``aegis.security`` logger), with the request ID and no secret material.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from aegis.core.sanitize import single_line
from aegis.db.tables import audit_events
from aegis.observability.context import correlation, request_id_var

security_log = logging.getLogger("aegis.security")

MAX_DETAILS_BYTES = 8_192


class Action(StrEnum):
    # authentication and sessions
    LOGIN = "auth.login"
    LOGOUT = "auth.logout"
    SESSION_REVOKED = "auth.session_revoked"
    # organization and membership
    INVITATION_CREATED = "membership.invitation_created"
    INVITATION_REVOKED = "membership.invitation_revoked"
    INVITATION_ACCEPTED = "membership.invitation_accepted"
    MEMBERSHIP_ROLE_CHANGED = "membership.role_changed"
    MEMBERSHIP_REMOVED = "membership.removed"
    ORG_SETTINGS_CHANGED = "org.settings_changed"
    # API keys
    API_KEY_CREATED = "api_key.created"
    API_KEY_ROTATED = "api_key.rotated"
    API_KEY_REVOKED = "api_key.revoked"
    # incidents and ingestion
    INCIDENT_CREATED = "incident.created"
    INCIDENT_UPDATED = "incident.updated"
    INCIDENT_AUTO_RESOLVED = "incident.auto_resolved"
    ALERTS_INGESTED = "alerts.ingested"
    CHANGE_EVENT_INGESTED = "change_event.ingested"
    # investigations
    INVESTIGATION_STARTED = "investigation.started"
    INVESTIGATION_DEFERRED = "investigation.deferred"
    INVESTIGATION_SKIPPED = "investigation.skipped"
    INVESTIGATION_CANCEL_REQUESTED = "investigation.cancel_requested"
    INVESTIGATION_CANCELLED = "investigation.cancelled"
    INVESTIGATION_RETRIED = "investigation.retried"
    INVESTIGATION_COMPLETED = "investigation.completed"
    INVESTIGATION_FAILED = "investigation.failed"
    EVIDENCE_COLLECTED = "investigation.evidence_collected"
    # AI layer (M3), remediation (M6), integrations (M4) use the same recorder
    MODEL_SELECTED = "ai.model_selected"
    MODEL_OUTPUT_ACCEPTED = "ai.output_accepted"
    MODEL_OUTPUT_REJECTED = "ai.output_rejected"
    INTEGRATION_CREATED = "integration.created"
    INTEGRATION_UPDATED = "integration.updated"
    INTEGRATION_DELETED = "integration.deleted"
    REMEDIATION_PROPOSED = "remediation.proposed"
    REMEDIATION_APPROVED = "remediation.approved"
    REMEDIATION_REJECTED = "remediation.rejected"
    REMEDIATION_EXECUTED = "remediation.executed"
    REMEDIATION_VERIFIED = "remediation.verified"
    REMEDIATION_ROLLED_BACK = "remediation.rolled_back"
    CONFIG_CHANGED = "config.changed"


@dataclass(frozen=True)
class Actor:
    type: str  # "user" | "api_key" | "system"
    id: str | None

    @classmethod
    def system(cls, component: str) -> Actor:
        return cls("system", component)


def _bounded(details: dict[str, Any] | None) -> dict[str, Any]:
    payload = details or {}
    encoded = json.dumps(payload, default=str, sort_keys=True)
    if len(encoded.encode("utf-8")) <= MAX_DETAILS_BYTES:
        normalised: dict[str, Any] = json.loads(encoded)  # UUIDs, datetimes -> str
        return normalised
    return {"truncated": True, "keys": sorted(payload)[:50]}


async def record(
    conn: AsyncConnection,
    org_id: UUID,
    actor: Actor,
    action: Action,
    *,
    target_type: str | None = None,
    target_id: str | UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        insert(audit_events).values(
            org_id=org_id,
            actor_type=actor.type,
            actor_id=single_line(actor.id)[:200] if actor.id else None,
            action=action.value,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=_bounded(details),
            request_id=request_id_var.get(),
        )
    )


def security_event(event: str, **fields: str | int | None) -> None:
    """Pre-tenant security event (no organization yet): structured log, never a secret."""
    security_log.warning(event, extra={"security_event": event, **correlation(), **fields})
