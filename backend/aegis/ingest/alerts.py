"""Alert payload validation and normalisation (pure; no I/O).

Payloads are untrusted even from an authenticated sender: the API key determines the
organization, never the payload; every string is bounded and stripped of structure-forging
characters; ``generatorURL`` is kept only if it is an http(s) URL without credentials.

Service identification uses the first present label of :data:`SERVICE_LABELS`; environment
uses :data:`ENVIRONMENT_LABELS`. Severity is normalised by :data:`SEVERITY_MAP`; anything
unrecognised becomes ``medium`` with the original label preserved.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from aegis.core.sanitize import clean_name, clean_text

MAX_ALERTS_PER_PAYLOAD = 200
MAX_LABELS = 64
MAX_LABEL_KEY = 100
MAX_LABEL_VALUE = 1024
MAX_ANNOTATION_VALUE = 4096
FUTURE_SKEW = timedelta(minutes=5)

SERVICE_LABELS = (
    "service",
    "service_name",
    "service.name",
    "app_kubernetes_io_name",
    "app.kubernetes.io/name",
    "app",
    "job",
    "container",
)
ENVIRONMENT_LABELS = ("environment", "env", "deployment_environment", "deployment.environment")
NAME_LABELS = ("alertname", "name")

SEVERITY_MAP = {
    "critical": "critical",
    "crit": "critical",
    "fatal": "critical",
    "emergency": "critical",
    "p1": "critical",
    "sev1": "critical",
    "page": "critical",
    "high": "high",
    "error": "high",
    "err": "high",
    "major": "high",
    "p2": "high",
    "sev2": "high",
    "warning": "medium",
    "warn": "medium",
    "medium": "medium",
    "moderate": "medium",
    "p3": "medium",
    "sev3": "medium",
    "low": "low",
    "minor": "low",
    "p4": "low",
    "sev4": "low",
    "info": "info",
    "informational": "info",
    "none": "info",
    "p5": "info",
    "sev5": "info",
}
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_FINGERPRINT_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")


def _bounded_map(raw: dict[str, Any], value_cap: int, multiline: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in sorted(raw)[:MAX_LABELS]:
        v = raw[k]
        if not isinstance(v, str):
            v = str(v)
        key = clean_name(str(k)[:MAX_LABEL_KEY])
        out[key] = clean_text(v[:value_cap]) if multiline else clean_name(v[:value_cap])
    return out


def safe_url(value: str | None) -> str | None:
    if not value or len(value) > 2000:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        return None
    return clean_name(value)


def normalise_severity(raw: str | None) -> str:
    return SEVERITY_MAP.get((raw or "").strip().lower(), "medium")


@dataclass(frozen=True)
class NormalizedAlert:
    source: Literal["alertmanager", "webhook"]
    fingerprint: str
    status: Literal["firing", "resolved"]
    name: str
    severity: str
    service: str | None
    environment: str | None
    labels: dict[str, str]
    annotations: dict[str, str]
    generator_url: str | None
    # As reported by the sender (None if not reported). This is the dedup identity together
    # with the fingerprint: clamping it would give every re-delivery a new identity.
    reported_start: datetime | None
    # Used for the incident start and investigation windows: never in the future.
    effective_start: datetime
    ends_at: datetime | None
    clock_skew_clamped: bool

    @property
    def group_key(self) -> str:
        basis = f"{self.service or '-'}\x1f{self.environment or '-'}"
        return hashlib.sha256(basis.encode()).hexdigest()[:32]


def _first(labels: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = labels.get(k)
        if v:
            return v[:200]
    return None


def _fingerprint(provided: str | None, labels: dict[str, str]) -> str:
    if provided and 1 <= len(provided) <= 128 and set(provided) <= _FINGERPRINT_OK:
        return provided
    canonical = "\x1e".join(f"{k}\x1f{labels[k]}" for k in sorted(labels))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _times(
    starts: datetime | None, ends: datetime | None, now: datetime
) -> tuple[datetime | None, datetime, datetime | None, bool]:
    """(reported start, effective start, end, clamped)."""
    # Alertmanager encodes "unset" as 0001-01-01T00:00:00Z
    reported = None if starts is None or starts.year < 2000 else starts
    clamped = reported is not None and reported > now + FUTURE_SKEW
    effective = now if reported is None or clamped else reported
    if ends is not None and (ends.year < 2000 or ends < (reported or effective)):
        ends = None
    return reported, effective, ends, clamped


def _build(
    source: Literal["alertmanager", "webhook"],
    *,
    status: str,
    labels_raw: dict[str, Any],
    annotations_raw: dict[str, Any],
    starts: datetime | None,
    ends: datetime | None,
    generator_url: str | None,
    fingerprint: str | None,
    now: datetime,
    name: str | None = None,
    severity: str | None = None,
    service: str | None = None,
    environment: str | None = None,
) -> NormalizedAlert:
    labels = _bounded_map(labels_raw, MAX_LABEL_VALUE, multiline=False)
    annotations = _bounded_map(annotations_raw, MAX_ANNOTATION_VALUE, multiline=True)
    reported, effective, ends_at, clamped = _times(starts, ends, now)
    st: Literal["firing", "resolved"] = "resolved" if status == "resolved" else "firing"
    return NormalizedAlert(
        source=source,
        fingerprint=_fingerprint(fingerprint, labels),
        status=st,
        name=clean_name((name or _first(labels, NAME_LABELS) or "unnamed alert")[:200]),
        severity=normalise_severity(severity or labels.get("severity") or labels.get("priority")),
        service=clean_name(service[:200]) if service else _first(labels, SERVICE_LABELS),
        environment=clean_name(environment[:100])
        if environment
        else _first(labels, ENVIRONMENT_LABELS),
        labels=labels,
        annotations=annotations,
        generator_url=safe_url(generator_url),
        reported_start=reported,
        effective_start=effective,
        ends_at=ends_at,
        clock_skew_clamped=clamped,
    )


# --- wire formats -------------------------------------------------------------------------


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore")


Labels = Annotated[dict[str, Any], Field(max_length=MAX_LABELS * 2)]


class AlertmanagerAlert(_Wire):
    status: Literal["firing", "resolved"] = "firing"
    labels: Labels = Field(default_factory=dict)
    annotations: Labels = Field(default_factory=dict)
    startsAt: AwareDatetime | None = None
    endsAt: AwareDatetime | None = None
    generatorURL: Annotated[str, Field(max_length=4000)] | None = None
    fingerprint: Annotated[str, Field(max_length=200)] | None = None


class AlertmanagerPayload(_Wire):
    version: Annotated[str, Field(max_length=10)] | None = None
    status: Literal["firing", "resolved"] | None = None
    receiver: Annotated[str, Field(max_length=500)] | None = None
    alerts: Annotated[
        list[AlertmanagerAlert], Field(min_length=1, max_length=MAX_ALERTS_PER_PAYLOAD)
    ]

    @field_validator("version")
    @classmethod
    def _v4(cls, v: str | None) -> str | None:
        if v is not None and v != "4":
            raise ValueError("only Alertmanager webhook payload version 4 is supported")
        return v


class GenericAlert(_Wire):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    status: Literal["firing", "resolved"] = "firing"
    severity: Annotated[str, Field(max_length=50)] | None = None
    service: Annotated[str, Field(max_length=200)] | None = None
    environment: Annotated[str, Field(max_length=100)] | None = None
    summary: Annotated[str, Field(max_length=MAX_ANNOTATION_VALUE)] | None = None
    description: Annotated[str, Field(max_length=MAX_ANNOTATION_VALUE)] | None = None
    labels: Labels = Field(default_factory=dict)
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    fingerprint: Annotated[str, Field(max_length=200)] | None = None
    url: Annotated[str, Field(max_length=4000)] | None = None


class GenericPayload(_Wire):
    alerts: Annotated[list[GenericAlert], Field(min_length=1, max_length=MAX_ALERTS_PER_PAYLOAD)]


def from_alertmanager(
    payload: AlertmanagerPayload, now: datetime | None = None
) -> list[NormalizedAlert]:
    now = now or datetime.now(UTC)
    return [
        _build(
            "alertmanager",
            status=a.status,
            labels_raw=a.labels,
            annotations_raw=a.annotations,
            starts=a.startsAt,
            ends=a.endsAt,
            generator_url=a.generatorURL,
            fingerprint=a.fingerprint,
            now=now,
        )
        for a in payload.alerts
    ]


def from_generic(payload: GenericPayload, now: datetime | None = None) -> list[NormalizedAlert]:
    now = now or datetime.now(UTC)
    out = []
    for a in payload.alerts:
        annotations = {
            k: v for k, v in (("summary", a.summary), ("description", a.description)) if v
        }
        out.append(
            _build(
                "webhook",
                status=a.status,
                labels_raw=a.labels,
                annotations_raw=annotations,
                starts=a.started_at,
                ends=a.ended_at,
                generator_url=a.url,
                fingerprint=a.fingerprint
                or _fingerprint(
                    None,
                    {
                        "name": a.name,
                        **{k: str(v) for k, v in a.labels.items()},
                        "service": a.service or "",
                        "environment": a.environment or "",
                    },
                ),
                now=now,
                name=a.name,
                severity=a.severity,
                service=a.service,
                environment=a.environment,
            )
        )
    return out
