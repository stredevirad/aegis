from datetime import datetime, timedelta, timezone

import httpx

from src.adapters.jaeger import JaegerProvider
from src.adapters.opensearch import OpenSearchProvider
from src.adapters.prometheus import PrometheusProvider
from src.domain import EvidenceType


def test_prometheus_evidence_provenance(monkeypatch):
    provider = PrometheusProvider(endpoint="http://mock")
    # Mock httpx.Client.get
    class MockResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"job": "test"}, "value": [1600000000, "1.5"]}
                    ]
                }
            }
    
    monkeypatch.setattr("httpx.Client.get", lambda *a, **kw: MockResponse())
    
    # provider.query_metrics returns a list of evidence
    res = provider.query_metrics("test_query", datetime.now(timezone.utc) - timedelta(minutes=5), datetime.now(timezone.utc))
    assert len(res) == 1
    ev = res[0]
    
    assert ev.source_provider == "prometheus"
    assert ev.type == EvidenceType.METRIC
    assert ev.observed_at is not None
    assert ev.collected_at is not None
    assert ev.service_resource == "test"

def test_opensearch_evidence_provenance(monkeypatch):
    provider = OpenSearchProvider(endpoint="http://mock")
    class MockResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {
                "hits": {
                    "hits": [
                        {"_source": {"@timestamp": "2023-01-01T12:00:00Z", "message": "error", "service.name": "svc"}}
                    ]
                }
            }
    
    monkeypatch.setattr("httpx.Client.post", lambda *a, **kw: MockResponse())
    
    res = provider.search_logs("test_query", datetime.now(timezone.utc) - timedelta(minutes=5), datetime.now(timezone.utc))
    assert len(res) == 1
    ev = res[0]
    
    assert ev.source_provider == "opensearch"
    assert ev.type == EvidenceType.LOG
    assert ev.observed_at is not None
    assert ev.collected_at is not None
    assert ev.service_resource == "svc"

def test_jaeger_evidence_provenance(monkeypatch):
    provider = JaegerProvider(endpoint="http://mock")
    class MockResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {
                "data": [
                    {
                        "traceID": "t1",
                        "spans": [{"startTime": 1600000000000, "duration": 1000, "processID": "p1"}],
                        "processes": {"p1": {"serviceName": "svc"}}
                    }
                ]
            }
    
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: MockResponse())
    
    res = provider.get_related_traces("svc", datetime.now(timezone.utc) - timedelta(minutes=5), datetime.now(timezone.utc))
    assert len(res) == 1
    ev = res[0]
    
    assert ev.source_provider == "jaeger"
    assert ev.type == EvidenceType.TRACE
    assert ev.observed_at is not None
    assert ev.collected_at is not None
    assert ev.service_resource == "svc"
