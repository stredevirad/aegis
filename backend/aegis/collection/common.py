"""Shared pieces of the telemetry adapters.

**Query construction** (audit S-C3, S-H6): queries are built only from operator configuration
(metric names, label names, index names — validated at load) and from *escaped* values
(service names). Service names are escaped for an RE2 regular expression and then for a
double-quoted PromQL/LogQL string literal, so a hostile name such as
``x"} or on() secret_metric{a=~".*`` stays a literal inside the matcher. Nothing that reaches a
query was chosen by a model, an alert payload or a tenant as free text.

**Outcomes**: every adapter reports ``ok`` / ``partial`` / ``failed`` with an item count and a
``truncated`` flag; "the backend answered with nothing" (ok, 0 items) is never confused with
"the backend could not be asked" (failed).

**Redaction** happens here, at collection time, before anything is stored or prompted.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aegis.core.model import (
    MAX_ATTR_VALUE,
    MAX_ATTRIBUTES,
    MAX_NAME,
    MAX_TEXT,
    CollectionStatus,
    LogRecord,
    MetricSeries,
    Modality,
    Span,
)
from aegis.core.sanitize import clean_name
from aegis.security.redact import redact

_RE2_META = re.compile(r"([\\.+*?()|\[\]{}^$])")
LABEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,99}$")
FIELD_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")


def re2_literal(value: str) -> str:
    """Escape a value so it matches only itself inside an RE2 regular expression."""
    return _RE2_META.sub(r"\\\1", value)


def string_literal(value: str) -> str:
    """Escape for the inside of a double-quoted PromQL/LogQL string."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def services_matcher(services: list[str]) -> str:
    """Body of a ``label=~"…"`` matcher matching exactly these services (or all, if empty)."""
    if not services:
        return ".+"
    return string_literal("|".join(re2_literal(s) for s in sorted(set(services))))


@dataclass
class RedactionTally:
    counts: Counter[str] = field(default_factory=Counter)

    def text(self, value: str, cap: int = MAX_TEXT) -> str:
        r = redact(value[: cap * 2])
        self.counts.update(r.counts)
        return r.text[:cap]

    def name(self, value: str) -> str:
        return clean_name(self.text(value, MAX_NAME))


def bounded_attributes(pairs: dict[str, Any], tally: RedactionTally) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in pairs.items():
        if len(out) >= MAX_ATTRIBUTES or v is None:
            continue
        out[clean_name(str(k)[:MAX_NAME])] = clean_name(tally.text(str(v), MAX_ATTR_VALUE))
    return out


@dataclass
class AdapterResult:
    modality: Modality
    provider: str  # connector id
    status: CollectionStatus = CollectionStatus.OK
    detail: str = ""
    truncated: bool = False
    metrics: list[MetricSeries] = field(default_factory=list)
    logs: list[LogRecord] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)

    @property
    def items(self) -> int:
        return len(self.metrics) + len(self.logs) + len(self.spans)

    def degrade(self, detail: str) -> None:
        """Some sub-queries failed: OK becomes PARTIAL (FAILED stays FAILED)."""
        if self.status is CollectionStatus.OK:
            self.status = CollectionStatus.PARTIAL
        self.detail = (self.detail + "; " if self.detail else "") + detail


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)
