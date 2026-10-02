"""Server-side role-based access control (security model C5).

The matrix below is the single source of truth; every API route declares the permission it
needs (a test fails if a route under ``/api/v1`` declares none), and the role/route matrix is
tested route by route.

Decisions encoded here:

* **Viewers cannot cause egress or model spend.** Starting, retrying or cancelling an
  investigation is an investigator-level action (ADR-011 amendment). Viewers read.
* **Reviewers** review conclusions and remediation: they read, may change an incident's
  lifecycle (resolve/close), read the audit log and approve/reject remediation, but do not
  start investigations.
* **Machine credentials (API keys) carry scopes, never roles.** ``ingest:alerts`` and
  ``ingest:changes`` grant exactly one write each; ``read`` grants viewer-level reads. No
  scope maps to an administrative permission, so a leaked ingestion key cannot become an
  administrator.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    INVESTIGATOR = "investigator"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class Scope(StrEnum):
    INGEST_ALERTS = "ingest:alerts"
    INGEST_CHANGES = "ingest:changes"
    READ = "read"


class Permission(StrEnum):
    INCIDENT_READ = "incident.read"
    INCIDENT_WRITE = "incident.write"  # create manually, edit title/severity/status
    INVESTIGATION_READ = "investigation.read"
    INVESTIGATION_START = "investigation.start"  # causes egress and possibly LLM spend
    INVESTIGATION_CANCEL = "investigation.cancel"
    EVIDENCE_READ = "evidence.read"
    CHANGE_READ = "change.read"
    CHANGE_WRITE = "change.write"
    ALERT_INGEST = "alert.ingest"
    AUDIT_READ = "audit.read"
    ORG_READ = "org.read"
    ORG_MANAGE = "org.manage"
    MEMBER_MANAGE = "member.manage"
    APIKEY_MANAGE = "apikey.manage"
    INTEGRATION_READ = "integration.read"
    INTEGRATION_MANAGE = "integration.manage"
    REMEDIATION_PROPOSE = "remediation.propose"
    REMEDIATION_APPROVE = "remediation.approve"
    REMEDIATION_EXECUTE = "remediation.execute"
    REMEDIATION_MANAGE = "remediation.manage"  # the action catalog (admin)


_READ = frozenset(
    {
        Permission.INCIDENT_READ,
        Permission.INVESTIGATION_READ,
        Permission.EVIDENCE_READ,
        Permission.CHANGE_READ,
        Permission.ORG_READ,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _READ,
    Role.REVIEWER: _READ
    | {
        Permission.INCIDENT_WRITE,
        Permission.AUDIT_READ,
        Permission.REMEDIATION_APPROVE,
        Permission.INTEGRATION_READ,
    },
    Role.INVESTIGATOR: _READ
    | {
        Permission.INCIDENT_WRITE,
        Permission.INVESTIGATION_START,
        Permission.INVESTIGATION_CANCEL,
        Permission.CHANGE_WRITE,
        Permission.REMEDIATION_PROPOSE,
        Permission.REMEDIATION_EXECUTE,
        Permission.INTEGRATION_READ,
    },
    Role.ADMIN: frozenset(Permission) - {Permission.ALERT_INGEST},
}

SCOPE_PERMISSIONS: dict[Scope, frozenset[Permission]] = {
    Scope.INGEST_ALERTS: frozenset({Permission.ALERT_INGEST}),
    Scope.INGEST_CHANGES: frozenset({Permission.CHANGE_WRITE}),
    Scope.READ: _READ,
}

# Permissions that no API key may ever hold, whatever the scope table says (defence in depth
# against a future edit of SCOPE_PERMISSIONS).
NEVER_FOR_KEYS = frozenset(
    {
        Permission.ORG_MANAGE,
        Permission.MEMBER_MANAGE,
        Permission.APIKEY_MANAGE,
        Permission.INTEGRATION_MANAGE,
        Permission.REMEDIATION_APPROVE,
        Permission.REMEDIATION_EXECUTE,
        Permission.REMEDIATION_MANAGE,
        Permission.REMEDIATION_PROPOSE,
        Permission.INVESTIGATION_START,
        Permission.AUDIT_READ,
    }
)


def permissions_for_role(role: str) -> frozenset[Permission]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return frozenset()


def permissions_for_scopes(scopes: list[str]) -> frozenset[Permission]:
    out: set[Permission] = set()
    for s in scopes:
        try:
            out |= SCOPE_PERMISSIONS[Scope(s)]
        except ValueError:
            continue
    return frozenset(out - NEVER_FOR_KEYS)
