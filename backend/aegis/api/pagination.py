"""Keyset (cursor) pagination.

Cursors are opaque base64url-encoded JSON of the last row's sort key and ID. They carry no
authority: every page query is still scoped to the caller's organization by RLS and by an
explicit ``org_id`` predicate, so a forged cursor can only skip within the caller's own data.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from aegis.api.errors import ApiError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def encode(*values: Any) -> str:
    raw = json.dumps(
        [
            v.isoformat() if isinstance(v, datetime) else str(v) if v is not None else None
            for v in values
        ]
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode(cursor: str | None, arity: int) -> list[str | None] | None:
    if not cursor:
        return None
    if len(cursor) > 512:
        raise ApiError(400, "invalid_cursor", "The cursor is invalid")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "invalid_cursor", "The cursor is invalid") from None
    if not isinstance(values, list) or len(values) != arity:
        raise ApiError(400, "invalid_cursor", "The cursor is invalid")
    return [None if v is None else str(v) for v in values]


def parse_dt(value: str | None) -> datetime:
    try:
        dt = datetime.fromisoformat(value or "")
    except ValueError:
        raise ApiError(400, "invalid_cursor", "The cursor is invalid") from None
    if dt.tzinfo is None:
        raise ApiError(400, "invalid_cursor", "The cursor is invalid")
    return dt
