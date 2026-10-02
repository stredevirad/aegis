"""Structured JSON logging with correlation IDs and redaction (prompt §48, security model §8).

Every record is one JSON object with ``ts``, ``level``, ``logger``, ``msg``, the correlation
identifiers in context (request, organization, investigation, job) and any ``extra`` fields.
The message, string extras and the formatted exception (tracebacks repeat exception messages,
which can contain URLs with credentials) all pass through :func:`aegis.security.redact` before
they are written. Logs carry identifiers, never telemetry payloads or prompts.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from aegis.observability.context import correlation
from aegis.security.redact import redact_text

_STANDARD = set(vars(logging.LogRecord("x", 0, "x", 0, "x", None, None))) | {
    "message",
    "asctime",
    "taskName",
}
MAX_EXC = 8_000
MAX_FIELD = 2_000


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage())[:MAX_FIELD],
        }
        out.update(correlation())
        for key, value in record.__dict__.items():
            if key in _STANDARD or key.startswith("_") or key in out:
                continue
            if isinstance(value, str):
                out[key] = redact_text(value)[:MAX_FIELD]
            elif isinstance(value, int | float | bool) or value is None:
                out[key] = value
            else:
                out[key] = redact_text(str(value))[:MAX_FIELD]
        if record.exc_info:
            out["exc"] = redact_text(self.formatException(record.exc_info))[-MAX_EXC:]
        return json.dumps(out, default=str)


def configure(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # third-party loggers that would otherwise log request lines or SQL
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
