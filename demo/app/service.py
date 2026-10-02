"""A small instrumented demo service (one image, role chosen by environment).

    SERVICE_NAME=frontend CALLS=checkout=http://checkout:8080,catalog=http://catalog:8080
    DB_CALLS=postgres            # simulated, uninstrumented database dependency
    FLAG_SERVICE_URL=http://flags:8080

Every request to ``/api/work`` opens a SERVER span, calls each downstream service with a
CLIENT span (W3C trace context propagated), records ``http.server.request.duration`` and
writes trace-correlated logs. Faults are driven by flags read from the flag service:

    <service>.error_rate   0..1   share of requests that fail with HTTP 500
    <service>.latency_ms   int    added latency

Instrumentation is done with the OpenTelemetry SDK directly (no auto-instrumentation) so the
telemetry shape is explicit: span status OK/ERROR, span kind, ``peer.service`` on client
spans, ``http.response.status_code`` on spans and metrics.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from opentelemetry import metrics, propagate, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

SERVICE = os.environ["SERVICE_NAME"]
INSTANCE = os.environ.get("HOSTNAME", SERVICE)
CALLS = [tuple(c.split("=", 1)) for c in os.environ.get("CALLS", "").split(",") if "=" in c]
DB_CALLS = [d for d in os.environ.get("DB_CALLS", "").split(",") if d]
FLAGS_URL = os.environ.get("FLAG_SERVICE_URL", "http://flags:8080")
BASE_LATENCY_MS = float(os.environ.get("BASE_LATENCY_MS", "8"))

resource = Resource.create(
    {"service.name": SERVICE, "service.instance.id": INSTANCE, "deployment.environment": "demo"}
)
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(tracer_provider)
reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=5000)
duration_view = View(
    instrument_name="http.server.request.duration",
    aggregation=ExplicitBucketHistogramAggregation([0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5]),
)
metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader], views=[duration_view]))
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
set_logger_provider(logger_provider)

log = logging.getLogger(SERVICE)
log.setLevel(logging.INFO)
log.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))
log.addHandler(logging.StreamHandler())

tracer = trace.get_tracer("aegis-demo")
meter = metrics.get_meter("aegis-demo")
server_duration = meter.create_histogram("http.server.request.duration", unit="s")
client_duration = meter.create_histogram("http.client.request.duration", unit="s")

_flags: dict[str, float] = {}
_flags_at = 0.0
http: httpx.AsyncClient


async def flag(name: str) -> float:
    global _flags, _flags_at
    if time.monotonic() - _flags_at > 1.0:
        try:
            r = await http.get(f"{FLAGS_URL}/flags", timeout=0.5)
            _flags = {k: float(v["value"]) for k, v in r.json().items() if _num(v["value"])}
        except (httpx.HTTPError, ValueError, KeyError):
            pass  # keep the last known flags
        _flags_at = time.monotonic()
    return _flags.get(f"{SERVICE}.{name}", 0.0)


def _num(v: object) -> bool:
    try:
        float(v)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False


@asynccontextmanager
async def lifespan(_: FastAPI):
    global http
    http = httpx.AsyncClient(timeout=5.0)
    yield
    await http.aclose()
    tracer_provider.shutdown()
    logger_provider.shutdown()


app = FastAPI(lifespan=lifespan, telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False})


async def call(name: str, url: str) -> int:
    started = time.perf_counter()
    with tracer.start_as_current_span(f"GET /api/work {name}", kind=SpanKind.CLIENT) as span:
        span.set_attribute("peer.service", name)
        span.set_attribute("http.request.method", "GET")
        headers: dict[str, str] = {}
        propagate.inject(headers)
        try:
            r = await http.get(f"{url}/api/work", headers=headers)
            status = r.status_code
        except httpx.HTTPError as exc:
            status = 599
            span.record_exception(exc)
        span.set_attribute("http.response.status_code", status)
        if status >= 500:
            span.set_status(Status(StatusCode.ERROR, f"HTTP {status}"))
        client_duration.record(time.perf_counter() - started, {"peer.service": name, "http.response.status_code": status})
        return status


async def db_call(name: str) -> None:
    with tracer.start_as_current_span(f"SELECT {name}", kind=SpanKind.CLIENT) as span:
        span.set_attribute("peer.service", name)
        span.set_attribute("db.system", "postgresql")
        await asyncio.sleep((2 + random.random() * 3 + await flag(f"{name}.latency_ms")) / 1000)  # noqa: S311


@app.get("/api/work")
async def work(request: Request) -> Response:
    started = time.perf_counter()
    ctx = propagate.extract(dict(request.headers))
    with tracer.start_as_current_span("GET /api/work", context=ctx, kind=SpanKind.SERVER) as span:
        span.set_attribute("http.request.method", "GET")
        span.set_attribute("http.route", "/api/work")
        await asyncio.sleep((BASE_LATENCY_MS + random.random() * 4 + await flag("latency_ms")) / 1000)  # noqa: S311
        for d in DB_CALLS:
            await db_call(d)
        statuses = await asyncio.gather(*(call(n, u) for n, u in CALLS)) if CALLS else []
        failed_downstream = [n for (n, _), s in zip(CALLS, statuses, strict=True) if s >= 500]
        status = 200
        if random.random() < await flag("error_rate"):  # noqa: S311
            status = 500
            log.error("request failed: inventory lookup error code=%d for item %d", random.randint(1, 3), random.randint(1000, 9999))  # noqa: S311
        elif failed_downstream:
            status = 502
            log.warning("downstream call failed: %s", ",".join(failed_downstream))
        span.set_attribute("http.response.status_code", status)
        if status >= 500:
            span.set_status(Status(StatusCode.ERROR, f"HTTP {status}"))
        else:
            span.set_status(Status(StatusCode.OK))
    server_duration.record(
        time.perf_counter() - started,
        {"http.route": "/api/work", "http.request.method": "GET", "http.response.status_code": status},
    )
    return Response(content=f'{{"service":"{SERVICE}","status":{status}}}', status_code=status, media_type="application/json")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE}
