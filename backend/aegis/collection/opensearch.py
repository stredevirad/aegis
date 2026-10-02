"""OpenSearch logs → log records, via a structured query (never ``query_string``).

The research adapter interpolated service names into Lucene ``query_string`` text, where
``x) OR (*`` neutralised the filter (audit S-C3). Here the query is the JSON query DSL with
``terms`` and ``range`` filters: values are data, not syntax. Index, timestamp, service,
severity, body and trace-ID field names come from the connector's operator options and are
validated; the index name is also checked by the egress path allowlist.

Options (defaults match the OpenTelemetry Collector's OpenSearch exporter as used by the
OpenTelemetry Demo): ``index`` (``otel-logs-*``), ``timestamp_field`` (``@timestamp``),
``service_field`` (``resource.service.name``), ``severity_field`` (``severityText``),
``body_field`` (``body``), ``trace_id_field`` (``traceId``), ``instance_field``
(``resource.service.instance.id``), ``max_hits`` (2000).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from aegis.collection.common import FIELD_NAME, AdapterResult, RedactionTally
from aegis.collection.loki import _SEVERITY
from aegis.core.model import CollectionStatus, LogRecord, Modality, Provenance, Severity
from aegis.integrations.egress import Credential, EgressClient, EgressDenied, EgressError
from aegis.integrations.registry import Connector

_INDEX = re.compile(r"^[a-z0-9][a-z0-9._*-]{0,99}$")
DEFAULTS = {
    "index": "otel-logs-*",
    "timestamp_field": "@timestamp",
    "service_field": "resource.service.name",
    "severity_field": "severityText",
    "body_field": "body",
    "trace_id_field": "traceId",
    "instance_field": "resource.service.instance.id",
}
WARN_AND_ABOVE = [
    "WARN",
    "WARNING",
    "Warning",
    "warn",
    "warning",
    "ERROR",
    "Error",
    "error",
    "FATAL",
    "Fatal",
    "fatal",
    "CRITICAL",
    "critical",
]


def _field(doc: Any, dotted: str) -> Any:
    """Look up a dotted field in nested and/or flattened documents
    (``{"resource": {"service.name": x}}``, ``{"resource.service.name": x}``, fully nested)."""
    if not isinstance(doc, dict):
        return None
    if dotted in doc:
        return doc[dotted]
    parts = dotted.split(".")
    for i in range(1, len(parts)):
        head = ".".join(parts[:i])
        if head in doc:
            found = _field(doc[head], ".".join(parts[i:]))
            if found is not None:
                return found
    return None


def options(connector: Connector) -> dict[str, Any]:
    o = {**DEFAULTS, **{k: v for k, v in connector.options.items() if k in DEFAULTS}}
    if not _INDEX.match(str(o["index"])):
        raise ValueError("invalid index")
    for k in DEFAULTS:
        if k != "index" and not FIELD_NAME.match(str(o[k])):
            raise ValueError(f"invalid {k}")
    o["max_hits"] = max(10, min(int(connector.options.get("max_hits", 2000)), 10_000))
    return o


def search_body(
    o: dict[str, Any], start: datetime, end: datetime, services: list[str]
) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [
        {"range": {o["timestamp_field"]: {"gte": start.isoformat(), "lt": end.isoformat()}}},
        {"terms": {o["severity_field"]: WARN_AND_ABOVE}},
    ]
    if services:
        filters.append({"terms": {o["service_field"]: sorted(set(services))[:50]}})
    return {
        "size": o["max_hits"],
        "sort": [{o["timestamp_field"]: {"order": "asc"}}],
        "track_total_hits": True,
        "query": {"bool": {"filter": filters}},
        "_source": [o[k] for k in DEFAULTS if k != "index"],
    }


def parse_hits(
    body: Any,
    o: dict[str, Any],
    connector: Connector,
    query: str,
    collected_at: datetime,
    tally: RedactionTally,
) -> tuple[list[LogRecord], int, int]:
    """(records, malformed, total matching hits)."""
    hits = (body.get("hits") or {}) if isinstance(body, dict) else {}
    total_raw = hits.get("total")
    total = int(total_raw.get("value", 0)) if isinstance(total_raw, dict) else int(total_raw or 0)
    out: list[LogRecord] = []
    bad = 0
    for h in hits.get("hits") or []:
        src = h.get("_source") or {}
        try:
            service = _field(src, o["service_field"])
            ts_raw = _field(src, o["timestamp_field"])
            if not service or not ts_raw:
                bad += 1
                continue
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            sev = _SEVERITY.get(
                str(_field(src, o["severity_field"]) or "").lower(), Severity.UNKNOWN
            )
            trace = _field(src, o["trace_id_field"])
            instance = _field(src, o["instance_field"])
            out.append(
                LogRecord(
                    ts=ts,
                    service=tally.name(str(service)),
                    severity=sev,
                    body=tally.text(str(_field(src, o["body_field"]) or "")),
                    instance=tally.name(str(instance)) if instance else None,
                    trace_id=str(trace)[:64]
                    if trace and all(c in "0123456789abcdefABCDEF" for c in str(trace))
                    else None,
                    provenance=Provenance(
                        provider=connector.id,
                        connector_id=connector.id,
                        query=query,
                        collected_at=collected_at,
                    ),
                )
            )
        except (TypeError, ValueError):
            bad += 1
    return out, bad, total


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
    try:
        o = options(connector)
    except ValueError as exc:
        result.status, result.detail = CollectionStatus.FAILED, f"connector options invalid: {exc}"
        return result
    failures = 0
    for name, start, end in windows:
        q = search_body(o, start, end, services)
        query = f"opensearch: index={o['index']} services={','.join(sorted(set(services)))[:300]} window={name} size={o['max_hits']}"
        try:
            resp = await egress.request(
                connector, "POST", f"/{o['index']}/_search", credential=credential, json_body=q
            )
            records, bad, total = parse_hits(
                resp.json(), o, connector, query[:2048], collected_at, tally
            )
        except EgressDenied as exc:
            result.status, result.detail = CollectionStatus.FAILED, f"denied: {exc.reason}"
            return result
        except EgressError as exc:
            failures += 1
            result.degrade(f"{name}: {exc.category}")
            continue
        if total > len(records) + bad:
            result.truncated = True
            result.degrade(f"{name}: {total} matching, {o['max_hits']} retrieved")
        if bad:
            result.degrade(f"{name}: {bad} malformed hit(s) skipped")
        result.logs.extend(records)
    if failures == len(windows):
        result.status = CollectionStatus.FAILED
    return result
