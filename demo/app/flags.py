"""Demo feature-flag service: the reversible fault and the remediation target.

    GET  /flags                 all flags {key: {value, version, updated_at, updated_by}}
    GET  /flags/{key}           one flag
    PUT|POST /flags/{key}       {"value": "...", "actor": "...", "if_version": n}
                                compare-and-set when if_version is given (409 on mismatch)
    GET  /healthz

Every change is reported to Aegis as a ``feature_flag`` change event (the change feed),
using an ingest key from ``AEGIS_CHANGE_KEY_FILE`` when configured. Flags are in memory: a
restart resets the demo to healthy.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("flags")
logging.basicConfig(level=logging.INFO)

AEGIS_URL = os.environ.get("AEGIS_URL", "")
KEY_FILE = os.environ.get("AEGIS_CHANGE_KEY_FILE", "")
_lock = threading.Lock()
_flags: dict[str, dict] = {}

app = FastAPI(telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False})


class FlagWrite(BaseModel):
    value: str = Field(max_length=100)
    actor: str = Field(default="unknown", max_length=100)
    if_version: int | None = None


def _key() -> str | None:
    if KEY_FILE and Path(KEY_FILE).is_file():
        return Path(KEY_FILE).read_text(encoding="utf-8").strip() or None
    return None


def _report(key: str, old: str | None, new: str, actor: str, version: int) -> None:
    api_key = _key()
    if not (AEGIS_URL and api_key):
        return
    service = key.split(".", 1)[0]
    event = {
        "source": "demo-flags",
        "kind": "feature_flag",
        "summary": f"flag {key} changed from {old!r} to {new!r} by {actor}",
        "ts": datetime.now(UTC).isoformat(),
        "service": service,
        "environment": "demo",
        "actor": actor,
        "external_id": f"{key}@{version}",
        "scope": {"flag_key": key, "new_value": new, "old_value": str(old)},
    }
    try:
        r = httpx.post(f"{AEGIS_URL}/api/v1/change-events", json=event, headers={"Authorization": f"Bearer {api_key}"}, timeout=3)
        log.info("change event reported: %s", r.status_code)
    except httpx.HTTPError as exc:
        log.warning("could not report change event: %s", type(exc).__name__)


@app.get("/flags")
def all_flags() -> dict[str, dict]:
    with _lock:
        return {k: dict(v) for k, v in _flags.items()}


@app.get("/flags/{key}")
def get_flag(key: str) -> dict:
    with _lock:
        if key not in _flags:
            return {"key": key, "value": None, "version": 0}
        return {"key": key, **_flags[key]}


@app.put("/flags/{key}")
@app.post("/flags/{key}")  # Aegis's egress client sends POST (no PUT)
def put_flag(key: str, body: FlagWrite) -> dict:
    with _lock:
        current = _flags.get(key)
        version = current["version"] if current else 0
        if body.if_version is not None and body.if_version != version:
            raise HTTPException(409, f"version mismatch: current {version}")
        old = current["value"] if current else None
        new_version = version + 1
        _flags[key] = {
            "value": body.value,
            "version": new_version,
            "updated_at": datetime.now(UTC).isoformat(),
            "updated_by": body.actor,
        }
    threading.Thread(target=_report, args=(key, old, body.value, body.actor, new_version), daemon=True).start()
    return {"key": key, **_flags[key]}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
