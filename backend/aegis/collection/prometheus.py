"""Prometheus metrics → per-service RED (+ saturation) signals.

Queries come from the connector's operator options (``options.queries``) or the defaults
below, which assume the OpenTelemetry HTTP server duration histogram exported through the
Collector's Prometheus exporter (``http_server_request_duration_seconds_*`` with a
``service_name`` label). Templates may use ``$label`` (the service label, validated as a
Prometheus label name), ``$services`` (an escaped regex alternation, or ``.+``) and
``$range`` (the rate window). Nothing else is substituted.

The error-ratio default fills missing numerators with zero (``or 0 * total``): without it a
service with no 5xx in the baseline has *no* error-ratio series at all, and the baseline is
silently lost — found by recording real responses from the demo stack.

Each point's ``count`` is the number of requests it summarises (rate * step), so the RCA
core can apply its minimum-count rules to low-traffic services.
"""

from __future__ import annotations

import math
import string
from datetime import datetime
from typing import Any

from aegis.collection.common import LABEL_NAME, AdapterResult, services_matcher, utc
from aegis.core.model import (
    MAX_POINTS_PER_SERIES,
    CollectionStatus,
    MetricPoint,
    MetricSeries,
    Modality,
    Provenance,
    SignalKind,
)
from aegis.integrations.egress import Credential, EgressClient, EgressDenied, EgressError
from aegis.integrations.registry import Connector

_HIST = "http_server_request_duration_seconds"
DEFAULT_QUERIES: dict[str, str] = {
    "request_rate": f'sum by ($label) (rate({_HIST}_count{{$label=~"$services"}}[$range]))',
    "error_ratio": (
        f'(sum by ($label) (rate({_HIST}_count{{$label=~"$services",http_response_status_code=~"5.."}}[$range]))'
        f' or (0 * sum by ($label) (rate({_HIST}_count{{$label=~"$services"}}[$range]))))'
        f' / sum by ($label) (rate({_HIST}_count{{$label=~"$services"}}[$range]))'
    ),
    "latency_p95_ms": (
        f'1000 * histogram_quantile(0.95, sum by ($label, le) (rate({_HIST}_bucket{{$label=~"$services"}}[$range])))'
    ),
}
_SIGNAL = {
    "request_rate": SignalKind.REQUEST_RATE,
    "error_ratio": SignalKind.ERROR_RATIO,
    "latency_p95_ms": SignalKind.LATENCY_P95_MS,
    "saturation": SignalKind.SATURATION,
}


class OptionsError(ValueError):
    pass


def _options(connector: Connector) -> tuple[str, dict[str, str], str, int]:
    o = connector.options
    label = str(o.get("service_label", "service_name"))
    if not LABEL_NAME.match(label):
        raise OptionsError("service_label must be a Prometheus label name")
    queries = dict(DEFAULT_QUERIES)
    for k, v in dict(o.get("queries") or {}).items():
        if k not in _SIGNAL or not isinstance(v, str) or len(v) > 2000:
            raise OptionsError(f"invalid query option {k!r}")
        queries[k] = v
    rate_range = str(o.get("rate_range", "1m"))
    if not rate_range[:-1].isdigit() or rate_range[-1] not in "smh":
        raise OptionsError("rate_range must look like 30s, 1m or 5m")
    max_series = int(o.get("max_series", 500))
    return label, queries, rate_range, max(1, min(max_series, 5000))


def render(template: str, label: str, services: list[str], rate_range: str) -> str:
    return string.Template(template).safe_substitute(
        label=label, services=services_matcher(services), range=rate_range
    )


def _points(values: list[Any], step: float) -> list[tuple[datetime, float]]:
    out = []
    for item in values[:MAX_POINTS_PER_SERIES]:
        try:
            ts, raw = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(raw) and math.isfinite(ts):
            out.append((utc(ts), raw))
    return out


async def collect(
    egress: EgressClient,
    connector: Connector,
    credential: Credential,
    *,
    start: datetime,
    end: datetime,
    step_seconds: int,
    services: list[str],
    collected_at: datetime,
) -> AdapterResult:
    result = AdapterResult(Modality.METRICS, connector.id)
    try:
        label, queries, rate_range, max_series = _options(connector)
    except (OptionsError, ValueError) as exc:
        result.status, result.detail = CollectionStatus.FAILED, f"connector options invalid: {exc}"
        return result
    span = end - start
    step = max(step_seconds, int(span.total_seconds() / 2000) + 1)  # keep ≤ ~2000 points per series
    raw: dict[str, dict[str, list[tuple[datetime, float]]]] = {}
    exprs: dict[str, str] = {}
    failures = 0
    for name, template in queries.items():
        expr = render(template, label, services, rate_range)
        exprs[name] = expr
        try:
            resp = await egress.request(
                connector,
                "GET",
                "/api/v1/query_range",
                credential=credential,
                params={
                    "query": expr,
                    "start": start.timestamp(),
                    "end": end.timestamp(),
                    "step": step,
                },
            )
            body = resp.json()
        except EgressDenied as exc:
            result.status, result.detail = CollectionStatus.FAILED, f"denied: {exc.reason}"
            return result
        except EgressError as exc:
            failures += 1
            result.degrade(f"{name}: {exc.category}")
            continue
        if not isinstance(body, dict) or body.get("status") != "success":
            failures += 1
            result.degrade(
                f"{name}: query error {str(body.get('errorType', 'unknown'))[:40] if isinstance(body, dict) else 'invalid'}"
            )
            continue
        series = (body.get("data") or {}).get("result") or []
        if len(series) > max_series:
            result.truncated = True
            series = series[:max_series]
        per_service: dict[str, list[tuple[datetime, float]]] = {}
        for s in series:
            svc = (s.get("metric") or {}).get(label)
            if isinstance(svc, str) and svc:
                per_service.setdefault(svc, []).extend(_points(s.get("values") or [], step))
        raw[name] = per_service
    if failures == len(queries):
        result.status = CollectionStatus.FAILED
        return result

    # request counts per (service, ts) for minimum-count rules
    counts: dict[tuple[str, datetime], float] = {
        (svc, ts): v * step for svc, pts in raw.get("request_rate", {}).items() for ts, v in pts
    }
    for name, per_service in raw.items():
        signal = _SIGNAL[name]
        for svc, pts in sorted(per_service.items()):
            if not pts:
                continue
            points = [
                MetricPoint(
                    ts=ts,
                    value=max(0.0, v),
                    count=counts.get((svc, ts))
                    if signal
                    in (SignalKind.ERROR_RATIO, SignalKind.REQUEST_RATE, SignalKind.LATENCY_P95_MS)
                    else None,
                )
                for ts, v in sorted(pts)
            ]
            result.metrics.append(
                MetricSeries(
                    service=svc[:200],
                    signal=signal,
                    points=points,
                    provenance=Provenance(
                        provider=connector.id,
                        connector_id=connector.id,
                        query=f"promql: {exprs[name]} step={step}s"[:2048],
                        collected_at=collected_at,
                    ),
                )
            )
    return result
