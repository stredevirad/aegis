from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from src.adapters.jaeger import JaegerProvider
from src.domain import EvidenceType, HealthStatus


def test_jaeger_health_healthy() -> None:
    provider = JaegerProvider("http://jaeger:16686")
    provider._normalize_trace = MagicMock() # type: ignore
    
    with pytest.MonkeyPatch.context() as m:
        mock_get = MagicMock()
        mock_get.return_value.status_code = 200
        m.setattr(httpx, "get", mock_get)
        
        status = provider.get_status()
        assert status.health == HealthStatus.HEALTHY
        mock_get.assert_called_once_with("http://jaeger:16686/jaeger/ui/api/services", timeout=5.0)

def test_jaeger_health_unavailable() -> None:
    provider = JaegerProvider("http://jaeger:16686")
    
    with pytest.MonkeyPatch.context() as m:
        mock_get = MagicMock(side_effect=httpx.RequestError("failed"))
        m.setattr(httpx, "get", mock_get)
        
        status = provider.get_status()
        assert status.health == HealthStatus.UNAVAILABLE

def test_jaeger_get_trace_success() -> None:
    provider = JaegerProvider("http://jaeger:16686")
    
    fake_trace_response = {
        "data": [
            {
                "traceID": "abc",
                "spans": [{"spanID": "1", "startTime": 1787498066606554, "duration": 14228}],
                "processes": {"p1": {"serviceName": "frontend"}}
            }
        ]
    }
    
    with pytest.MonkeyPatch.context() as m:
        mock_get = MagicMock()
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = fake_trace_response
        m.setattr(httpx, "get", mock_get)
        
        ev = provider.get_trace("abc")
        assert ev is not None
        assert ev.id == "ev_trace_abc"
        assert ev.type == EvidenceType.TRACE
        assert ev.source_provider == "jaeger"

def test_jaeger_get_trace_not_found() -> None:
    provider = JaegerProvider("http://jaeger:16686")
    
    with pytest.MonkeyPatch.context() as m:
        mock_get = MagicMock()
        mock_get.return_value.status_code = 404
        m.setattr(httpx, "get", mock_get)
        
        ev = provider.get_trace("abc")
        assert ev is None

def test_jaeger_get_related_traces_bounds() -> None:
    provider = JaegerProvider("http://jaeger:16686")
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(hours=13)
    
    with pytest.raises(ValueError, match="exceeds the 12-hour limit"):
        provider.get_related_traces("frontend", start_time, end_time)

    with pytest.raises(ValueError, match="start_time cannot be after end_time"):
        provider.get_related_traces("frontend", start_time, start_time - timedelta(hours=1))

def test_jaeger_get_related_traces_success() -> None:
    provider = JaegerProvider("http://jaeger:16686")
    
    fake_trace_response = {
        "data": [
            {
                "traceID": "abc",
                "spans": [{"spanID": "1", "startTime": 1787498066606554, "duration": 14228}],
                "processes": {"p1": {"serviceName": "frontend"}}
            }
        ]
    }
    
    with pytest.MonkeyPatch.context() as m:
        mock_get = MagicMock()
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = fake_trace_response
        m.setattr(httpx, "get", mock_get)
        
        start_time = datetime.now(timezone.utc) - timedelta(hours=1)
        end_time = datetime.now(timezone.utc)
        
        results = provider.get_related_traces("frontend", start_time, end_time)
        assert len(results) == 1
        assert results[0].id == "ev_trace_abc"
