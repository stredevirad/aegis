"""Jaeger (HTTP query API) traces → spans with status, kind, parents and peers.

The research adapter dropped every tag, so error spans were indistinguishable from successful
ones (audit AI-04). Here status is kept: ``otel.status_code`` (OK/ERROR) when present, else
the classic ``error=true`` tag, else UNSET (instrumentation reported no status — which the
core treats as "not an error", distinct from UNKNOWN = "the backend gave no status at all").

Trace search is per service and per window (incident and baseline separately, so baseline
error-origin rates and call patterns exist); traces are de-duplicated by ID across services.
Trace IDs used in paths are validated hex (egress path allowlist).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aegis.collection.common import AdapterResult, RedactionTally, bounded_attributes, utc
from aegis.core.model import CollectionStatus, Modality, Provenance, Span, SpanKind, SpanStatus
from aegis.integrations.egress import Credential, EgressClient, EgressDenied, EgressError
from aegis.integrations.registry import Connector

KEEP_TAGS = (
    "http.route",
    "http.request.method",
    "http.method",
    "db.system",
    "rpc.system",
    "rpc.service",
    "rpc.method",
    "messaging.system",
    "server.address",
    "net.peer.name",
)
_KINDS = {k.value: k for k in SpanKind}


def _tags(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {
        str(t["key"]): t.get("value") for t in (items or []) if isinstance(t, dict) and t.get("key")
    }


def _status(tags: dict[str, Any]) -> SpanStatus:
    code = str(tags.get("otel.status_code", "")).upper()
    if code == "ERROR":
        return SpanStatus.ERROR
    if code == "OK":
        return SpanStatus.OK
    if tags.get("error") in (True, "true", "True"):
        return SpanStatus.ERROR
    return SpanStatus.UNSET


def _http_status(tags: dict[str, Any]) -> int | None:
    for k in ("http.response.status_code", "http.status_code"):
        try:
            v = int(tags[k])
        except (KeyError, TypeError, ValueError):
            continue
        if 100 <= v <= 599:
            return v
    return None


def parse_traces(
    body: Any, connector: Connector, query: str, collected_at: datetime, tally: RedactionTally
) -> tuple[list[Span], int]:
    """Spans from a ``/api/traces`` response; returns (spans, malformed span count)."""
    spans: list[Span] = []
    bad = 0
    for trace in (body.get("data") if isinstance(body, dict) else None) or []:
        processes = trace.get("processes") or {}
        for s in trace.get("spans") or []:
            try:
                proc = processes.get(s.get("processID")) or {}
                ptags = _tags(proc.get("tags"))
                tags = _tags(s.get("tags"))
                parent = next(
                    (
                        r.get("spanID")
                        for r in s.get("references") or []
                        if r.get("refType") == "CHILD_OF"
                    ),
                    None,
                )
                attrs = {k: tags[k] for k in KEEP_TAGS if k in tags}
                if "service.instance.id" in ptags:
                    attrs["service.instance.id"] = ptags["service.instance.id"]
                peer = tags.get("peer.service")
                spans.append(
                    Span(
                        trace_id=str(s["traceID"])[:64],
                        span_id=str(s["spanID"])[:32],
                        parent_span_id=str(parent)[:32] if parent else None,
                        service=tally.name(str(proc.get("serviceName") or "unknown")),
                        operation=tally.name(str(s.get("operationName") or "unknown")),
                        kind=_KINDS.get(str(tags.get("span.kind", "")).lower(), SpanKind.UNKNOWN),
                        start=utc(int(s["startTime"]) / 1_000_000),
                        duration_ms=max(0.0, int(s.get("duration", 0)) / 1000.0),
                        status=_status(tags),
                        peer_service=tally.name(str(peer)) if peer else None,
                        http_status=_http_status(tags),
                        attributes=bounded_attributes(attrs, tally),
                        provenance=Provenance(
                            provider=connector.id,
                            connector_id=connector.id,
                            query=query,
                            collected_at=collected_at,
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError):
                bad += 1
    return spans, bad


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
    result = AdapterResult(Modality.TRACES, connector.id)
    limit = max(1, min(int(connector.options.get("traces_per_service", 100)), 1000))
    max_spans = max(100, min(int(connector.options.get("max_spans", 30_000)), 100_000))
    try:
        if not services:
            listed = (
                await egress.request(connector, "GET", "/api/services", credential=credential)
            ).json()
            services = sorted(s for s in (listed.get("data") or []) if isinstance(s, str))[:20]
    except EgressDenied as exc:
        result.status, result.detail = CollectionStatus.FAILED, f"denied: {exc.reason}"
        return result
    except EgressError as exc:
        result.status, result.detail = CollectionStatus.FAILED, f"service list: {exc.category}"
        return result
    seen: set[tuple[str, str]] = set()
    attempts = failures = malformed = 0
    for svc in services[:20]:
        for label, start, end in windows:
            attempts += 1
            query = f"jaeger: service={svc} window={label} limit={limit}"
            try:
                resp = await egress.request(
                    connector,
                    "GET",
                    "/api/traces",
                    credential=credential,
                    params={
                        "service": svc,
                        "start": int(start.timestamp() * 1e6),
                        "end": int(end.timestamp() * 1e6),
                        "limit": limit,
                    },
                )
                spans, bad = parse_traces(resp.json(), connector, query[:2048], collected_at, tally)
            except EgressDenied as exc:
                result.status, result.detail = CollectionStatus.FAILED, f"denied: {exc.reason}"
                return result
            except EgressError as exc:
                failures += 1
                result.degrade(f"{svc}/{label}: {exc.category}")
                continue
            malformed += bad
            traces_returned = len({s.trace_id for s in spans})
            if traces_returned >= limit:
                result.truncated = True
            for sp in spans:
                key = (sp.trace_id, sp.span_id)
                if key in seen:
                    continue
                if len(result.spans) >= max_spans:
                    result.truncated = True
                    break
                seen.add(key)
                result.spans.append(sp)
    if attempts and failures == attempts:
        result.status = CollectionStatus.FAILED
    if malformed:
        result.degrade(f"{malformed} malformed span(s) skipped")
    return result
