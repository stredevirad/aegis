"""Loki logs → log records (warn and above) with severity, instance and trace context.

LogQL is built from the operator's ``service_label`` (validated label name) and escaped service
names. Severity comes from OTLP structured metadata (``severity_text``), Loki's
``detected_level``, or a ``level`` label. Bodies are redacted at collection and bounded.
A window that returns ``limit`` lines is marked truncated (PARTIAL), never silently complete.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aegis.collection.common import LABEL_NAME, AdapterResult, RedactionTally, services_matcher, utc
from aegis.core.model import CollectionStatus, LogRecord, Modality, Provenance, Severity
from aegis.integrations.egress import Credential, EgressClient, EgressDenied, EgressError
from aegis.integrations.registry import Connector

_SEVERITY = {
    "trace": Severity.DEBUG,
    "debug": Severity.DEBUG,
    "info": Severity.INFO,
    "information": Severity.INFO,
    "warn": Severity.WARN,
    "warning": Severity.WARN,
    "error": Severity.ERROR,
    "err": Severity.ERROR,
    "fatal": Severity.FATAL,
    "critical": Severity.FATAL,
    "crit": Severity.FATAL,
    "panic": Severity.FATAL,
}
INSTANCE_KEYS = ("service_instance_id", "k8s_pod_name", "pod", "instance", "host_name", "host")


def severity_of(labels: dict[str, str]) -> Severity:
    for k in ("severity_text", "detected_level", "level", "severity"):
        v = labels.get(k)
        if v:
            return _SEVERITY.get(v.strip().lower(), Severity.UNKNOWN)
    return Severity.UNKNOWN


def logql(label: str, services: list[str]) -> str:
    return (
        f'{{{label}=~"{services_matcher(services)}"}}'
        ' | detected_level=~"(?i)(warn|warning|error|err|fatal|critical|crit|panic)"'
    )


def parse_streams(
    body: Any,
    label: str,
    connector: Connector,
    query: str,
    collected_at: datetime,
    tally: RedactionTally,
) -> tuple[list[LogRecord], int]:
    out: list[LogRecord] = []
    bad = 0
    for stream in (
        (body.get("data") or {}).get("result") if isinstance(body, dict) else None
    ) or []:
        labels = {str(k): str(v) for k, v in (stream.get("stream") or {}).items()}
        for entry in stream.get("values") or []:
            try:
                meta = {
                    str(k): str(v)
                    for k, v in (
                        entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
                    ).items()
                }
                merged = {**labels, **meta}
                service = merged.get(label)
                if not service:
                    bad += 1
                    continue
                trace_id = merged.get("trace_id") or merged.get("traceid")
                out.append(
                    LogRecord(
                        ts=utc(int(entry[0]) / 1e9),
                        service=tally.name(service),
                        severity=severity_of(merged),
                        body=tally.text(str(entry[1])),
                        instance=tally.name(
                            next((merged[k] for k in INSTANCE_KEYS if merged.get(k)), "")
                        )
                        or None,
                        trace_id=trace_id[:64]
                        if trace_id and all(c in "0123456789abcdefABCDEF" for c in trace_id)
                        else None,
                        provenance=Provenance(
                            provider=connector.id,
                            connector_id=connector.id,
                            query=query,
                            collected_at=collected_at,
                        ),
                    )
                )
            except (TypeError, ValueError, IndexError):
                bad += 1
    return out, bad


async def collect(
    egress: EgressClient,
    connector: Connector,
    credential: Credential,
    *,
    windows: list[tuple[str, datetime, datetime]],
    services: list[str],
    collected_at: datetime,
    tally: RedactionTally,
) -> AdapterResult:
    result = AdapterResult(Modality.LOGS, connector.id)
    label = str(connector.options.get("service_label", "service_name"))
    if not LABEL_NAME.match(label):
        result.status, result.detail = (
            CollectionStatus.FAILED,
            "connector options invalid: service_label",
        )
        return result
    limit = max(10, min(int(connector.options.get("max_lines", 2000)), 5000))
    expr = logql(label, services)
    failures = 0
    for name, start, end in windows:
        query = f"logql: {expr} window={name} limit={limit}"[:2048]
        try:
            resp = await egress.request(
                connector,
                "GET",
                "/loki/api/v1/query_range",
                credential=credential,
                params={
                    "query": expr,
                    "start": int(start.timestamp() * 1e9),
                    "end": int(end.timestamp() * 1e9),
                    "limit": limit,
                    "direction": "forward",
                },
            )
            records, bad = parse_streams(resp.json(), label, connector, query, collected_at, tally)
        except EgressDenied as exc:
            result.status, result.detail = CollectionStatus.FAILED, f"denied: {exc.reason}"
            return result
        except EgressError as exc:
            failures += 1
            result.degrade(f"{name}: {exc.category}")
            continue
        if len(records) + bad >= limit:
            result.truncated = True
            result.degrade(f"{name}: {limit}-line limit reached")
        if bad:
            result.degrade(f"{name}: {bad} unparseable line(s) skipped")
        result.logs.extend(records)
    if failures == len(windows):
        result.status = CollectionStatus.FAILED
    return result
