"""Contract tests for the telemetry adapters against responses recorded from the demo stack
(``deploy/compose.yaml --profile demo``, 2026-10-02, catalog fault at 30%). The OpenSearch
fixture is constructed from the documented response shape (no OpenSearch in the demo stack)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aegis.collection import jaeger, loki, opensearch, prometheus
from aegis.collection.common import RedactionTally, re2_literal, services_matcher
from aegis.core.model import CollectionStatus, Severity, SignalKind, SpanKind, SpanStatus
from aegis.integrations.egress import NO_CREDENTIAL, EgressError, EgressResponse, check_path
from aegis.integrations.registry import Connector, ConnectorKind

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 2, 0, 40, tzinfo=UTC)


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeEgress:
    """Serves recorded bodies; enforces the real path allowlist; records the calls."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    async def request(
        self, connector, method, path, *, params=None, json_body=None, credential=NO_CREDENTIAL, **_
    ):
        check_path(connector.kind, path)
        self.calls.append(
            {"method": method, "path": path, "params": dict(params or {}), "json": json_body}
        )
        body = self.responder(path, dict(params or {}), json_body)
        if isinstance(body, Exception):
            raise body
        return EgressResponse(200, {}, json.dumps(body).encode(), f"{method} {connector.id} {path}")


def conn(kind: ConnectorKind, **options) -> Connector:
    return Connector(
        id=f"{kind.value}-1",
        kind=kind,
        url="http://backend.test:9000",
        orgs=["acme"],
        options=options,
    )


# --- query construction ---------------------------------------------------------------------


def test_hostile_service_names_cannot_break_out_of_matchers():
    hostile = 'x"} or on() secret_metric{a=~".*'
    rendered = prometheus.render(
        prometheus.DEFAULT_QUERIES["request_rate"], "service_name", [hostile], "1m"
    )
    # the only unescaped double quotes are the ones delimiting the matcher
    unescaped = [i for i, ch in enumerate(rendered) if ch == '"' and rendered[i - 1] != "\\"]
    assert len(unescaped) == 2, rendered
    # regex escapes are themselves escaped inside the PromQL string literal
    assert r"x\"\\} or on\\(\\) secret_metric\\{a=~\"\\.\\*" in rendered
    assert services_matcher([]) == ".+"
    assert re2_literal("a.b|c") == "a\\.b\\|c"
    assert (
        loki.logql("service_name", [hostile]).count('"')
        - loki.logql("service_name", [hostile]).count('\\"')
        == 4
    )


def test_invalid_operator_options_fail_closed():
    for opts in (
        {"service_label": 'x"}'},
        {"queries": {"evil": "up"}},
        {"rate_range": "1m) or vector(1"},
    ):
        with pytest.raises(prometheus.OptionsError):
            prometheus._options(conn(ConnectorKind.PROMETHEUS, **opts))
    with pytest.raises(ValueError):
        opensearch.options(conn(ConnectorKind.OPENSEARCH, index="../_cluster"))
    with pytest.raises(ValueError):
        opensearch.options(conn(ConnectorKind.OPENSEARCH, service_field="a b"))


def test_opensearch_uses_structured_filters_never_query_string():
    o = opensearch.options(conn(ConnectorKind.OPENSEARCH))
    body = opensearch.search_body(o, NOW, NOW, ["x) OR (*", "catalog"])
    text = json.dumps(body)
    assert "query_string" not in text and "simple_query_string" not in text
    terms = [
        f
        for f in body["query"]["bool"]["filter"]
        if "terms" in f and "resource.service.name" in f["terms"]
    ]
    assert terms[0]["terms"]["resource.service.name"] == ["catalog", "x) OR (*"]


# --- Prometheus -----------------------------------------------------------------------------------


async def test_prometheus_recorded_responses():
    fixtures = {
        "5..": "prometheus_error_ratio.json",
        "histogram_quantile": "prometheus_latency_p95.json",
    }

    def respond(path, params, _):
        q = params["query"]
        for needle, f in fixtures.items():
            if needle in q:
                return load(f)
        return load("prometheus_request_rate.json")

    egress = FakeEgress(respond)
    start = datetime.fromtimestamp(1790900700, UTC)
    r = await prometheus.collect(
        egress,
        conn(ConnectorKind.PROMETHEUS),
        NO_CREDENTIAL,
        start=start,
        end=NOW,
        step_seconds=30,
        services=[],
        collected_at=NOW,
    )
    assert r.status is CollectionStatus.OK and len(egress.calls) == 3
    by = {(m.service, m.signal) for m in r.metrics}
    assert {
        ("catalog", SignalKind.REQUEST_RATE),
        ("catalog", SignalKind.ERROR_RATIO),
        ("frontend", SignalKind.LATENCY_P95_MS),
    } <= by
    ratio = next(
        m for m in r.metrics if m.service == "catalog" and m.signal is SignalKind.ERROR_RATIO
    )
    assert all(0 <= p.value <= 1 for p in ratio.points) and max(p.value for p in ratio.points) > 0.1
    assert any(p.count for p in ratio.points)  # weighted by request counts
    assert ratio.provenance.provider == "prometheus-1" and ratio.provenance.query.startswith(
        "promql:"
    )
    # one query fails -> partial; all fail -> failed; "nothing matched" -> ok with 0 items
    egress = FakeEgress(
        lambda p, q, _: (
            EgressError("timeout", "t")
            if "5.." in q["query"]
            else load("prometheus_request_rate.json")
        )
    )
    r = await prometheus.collect(
        egress,
        conn(ConnectorKind.PROMETHEUS),
        NO_CREDENTIAL,
        start=start,
        end=NOW,
        step_seconds=30,
        services=[],
        collected_at=NOW,
    )
    assert r.status is CollectionStatus.PARTIAL and "error_ratio: timeout" in r.detail
    r = await prometheus.collect(
        FakeEgress(lambda *a: EgressError("connect", "c")),
        conn(ConnectorKind.PROMETHEUS),
        NO_CREDENTIAL,
        start=start,
        end=NOW,
        step_seconds=30,
        services=[],
        collected_at=NOW,
    )
    assert r.status is CollectionStatus.FAILED
    r = await prometheus.collect(
        FakeEgress(lambda *a: load("prometheus_empty.json")),
        conn(ConnectorKind.PROMETHEUS),
        NO_CREDENTIAL,
        start=start,
        end=NOW,
        step_seconds=30,
        services=[],
        collected_at=NOW,
    )
    assert r.status is CollectionStatus.OK and r.items == 0
    r = await prometheus.collect(
        FakeEgress(lambda *a: load("prometheus_bad_query.json")),
        conn(ConnectorKind.PROMETHEUS),
        NO_CREDENTIAL,
        start=start,
        end=NOW,
        step_seconds=30,
        services=[],
        collected_at=NOW,
    )
    assert r.status is CollectionStatus.FAILED and "bad_data" in r.detail


def test_prometheus_nan_and_inf_points_are_dropped():
    pts = prometheus._points([[1, "NaN"], [2, "+Inf"], [3, "1.5"], [4, "-Inf"], ["x", "1"]], 30)
    assert [v for _, v in pts] == [1.5]


# --- Jaeger -----------------------------------------------------------------------------------------


def test_jaeger_recorded_traces_keep_status_parents_and_peers():
    spans, bad = jaeger.parse_traces(
        load("jaeger_traces_frontend.json"), conn(ConnectorKind.JAEGER), "q", NOW, RedactionTally()
    )
    assert bad == 0 and len(spans) > 50
    statuses = {s.status for s in spans}
    assert (
        SpanStatus.OK in statuses and SpanStatus.UNSET in statuses
    )  # db client spans carry no status
    assert {s.service for s in spans} >= {"frontend", "checkout", "catalog", "payments"}
    assert any(s.parent_span_id for s in spans) and any(s.kind is SpanKind.SERVER for s in spans)
    db = [s for s in spans if s.peer_service == "postgres"]
    assert db and all(
        s.kind is SpanKind.CLIENT and s.attributes.get("db.system") == "postgresql" for s in db
    )
    assert all("service.instance.id" in s.attributes for s in spans)
    errors = [s for s in spans if s.status is SpanStatus.ERROR]
    assert all(s.http_status and s.http_status >= 500 for s in errors)


def test_jaeger_status_mapping_and_hostile_names():
    trace = {
        "data": [
            {
                "traceID": "ab",
                "processes": {"p": {"serviceName": "evil\nsvc‮", "tags": []}},
                "spans": [
                    {
                        "traceID": "ab",
                        "spanID": "01",
                        "operationName": "op",
                        "startTime": 1,
                        "duration": 5,
                        "processID": "p",
                        "tags": [{"key": "error", "type": "bool", "value": True}],
                    },
                    {
                        "traceID": "ab",
                        "spanID": "02",
                        "operationName": "op",
                        "startTime": 1,
                        "duration": 5,
                        "processID": "p",
                        "tags": [
                            {"key": "otel.status_code", "value": "ERROR"},
                            {"key": "db.statement", "value": "password=hunter2"},
                        ],
                    },
                    {
                        "traceID": "ab",
                        "spanID": "03",
                        "startTime": "not-a-number",
                        "processID": "p",
                    },
                ],
            }
        ]
    }
    spans, bad = jaeger.parse_traces(trace, conn(ConnectorKind.JAEGER), "q", NOW, RedactionTally())
    assert bad == 1 and [s.status for s in spans] == [SpanStatus.ERROR, SpanStatus.ERROR]
    assert "\n" not in spans[0].service and "‮" not in spans[0].service
    assert "db.statement" not in spans[1].attributes  # only allowlisted tags are kept


# --- Loki ---------------------------------------------------------------------------------------------


def test_loki_recorded_logs_and_redaction():
    body = load("loki_logs.json")
    body["data"]["result"][0]["values"][0][1] = "connect failed password=hunter2 to db"
    tally = RedactionTally()
    logs, bad = loki.parse_streams(body, "service_name", conn(ConnectorKind.LOKI), "q", NOW, tally)
    assert bad == 0 and len(logs) == 60
    assert {lg.severity for lg in logs} <= {Severity.ERROR, Severity.WARN}
    assert any(lg.trace_id for lg in logs) and all(lg.instance for lg in logs)
    assert all("hunter2" not in lg.body for lg in logs) and tally.counts["secret"] == 1


async def test_loki_limit_is_reported_as_truncation():
    egress = FakeEgress(lambda *a: load("loki_logs.json"))  # 60 lines
    r = await loki.collect(
        egress,
        conn(ConnectorKind.LOKI, max_lines=60),
        NO_CREDENTIAL,
        windows=[("incident", NOW, NOW)],
        services=["catalog"],
        collected_at=NOW,
        tally=RedactionTally(),
    )
    assert r.truncated and r.status is CollectionStatus.PARTIAL and "limit" in r.detail
    assert 'service_name=~"catalog"' in egress.calls[0]["params"]["query"]


# --- OpenSearch (constructed fixture) -----------------------------------------------------------------


def test_opensearch_hits_parse_nested_and_flattened_fields():
    o = opensearch.options(conn(ConnectorKind.OPENSEARCH))
    body = {
        "hits": {
            "total": {"value": 3, "relation": "eq"},
            "hits": [
                {
                    "_source": {
                        "@timestamp": "2026-10-02T00:35:00.123Z",
                        "severityText": "ERROR",
                        "body": "boom token=abc123",
                        "traceId": "4bf92f3577b34da6a3ce929d0e0e4736",
                        "resource": {"service.name": "catalog", "service.instance.id": "c-1"},
                    }
                },
                {
                    "_source": {
                        "@timestamp": "2026-10-02T00:35:01Z",
                        "severityText": "Warning",
                        "body": "slow",
                        "resource.service.name": "checkout",
                    }
                },
                {"_source": {"severityText": "ERROR", "body": "no service or time"}},
            ],
        }
    }
    logs, bad, total = opensearch.parse_hits(
        body, o, conn(ConnectorKind.OPENSEARCH), "q", NOW, RedactionTally()
    )
    assert (len(logs), bad, total) == (2, 1, 3)
    assert (
        logs[0].service == "catalog"
        and logs[0].instance == "c-1"
        and logs[0].severity is Severity.ERROR
    )
    assert "abc123" not in logs[0].body and logs[1].severity is Severity.WARN
