"""Correlation identifiers carried through a request or a job (context variables).

Logs, audit records and investigation events read these so one request or job can be
followed end to end. Values are identifiers only — never payloads, prompts or secrets.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("aegis_request_id", default=None)
org_id_var: ContextVar[str | None] = ContextVar("aegis_org_id", default=None)
investigation_id_var: ContextVar[str | None] = ContextVar("aegis_investigation_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("aegis_job_id", default=None)

_INBOUND_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def accept_request_id(inbound: str | None) -> str:
    """Use a caller-supplied request ID only if it is a safe token; otherwise mint one."""
    if inbound and _INBOUND_REQUEST_ID.fullmatch(inbound):
        return inbound
    return uuid.uuid4().hex


def correlation() -> dict[str, str]:
    out = {}
    for key, var in (
        ("request_id", request_id_var),
        ("org_id", org_id_var),
        ("investigation_id", investigation_id_var),
        ("job_id", job_id_var),
    ):
        value = var.get()
        if value:
            out[key] = value
    return out
