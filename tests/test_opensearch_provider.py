from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from src.adapters.opensearch import OpenSearchProvider
from src.domain import EvidenceType, HealthStatus


def test_opensearch_health_healthy() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client.get.return_value = mock_response

    provider = OpenSearchProvider(client=mock_client)
    status = provider.get_status()

    assert status.health == HealthStatus.HEALTHY
    assert "search_logs" in status.capabilities

def test_opensearch_health_unavailable() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = httpx.RequestError("Connection Refused")

    provider = OpenSearchProvider(client=mock_client)
    status = provider.get_status()

    assert status.health == HealthStatus.UNAVAILABLE
    assert "OpenSearch unreachable" in status.limitations[0]

def test_search_logs_bounds_reversed() -> None:
    provider = OpenSearchProvider()
    start_time = datetime.now(timezone.utc)
    end_time = start_time - timedelta(minutes=5)

    with pytest.raises(ValueError, match="must be greater than or equal to start_time"):
        provider.search_logs("test", start_time, end_time)

def test_search_logs_bounds_exceeded() -> None:
    provider = OpenSearchProvider()
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(hours=13)

    with pytest.raises(ValueError, match="exceeds maximum allowed"):
        provider.search_logs("test", start_time, end_time)

def test_search_logs_success() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "hits": {
            "hits": [
                {
                    "_index": "otel-logs-2026-08-21",
                    "_id": "abc1234",
                    "_source": {
                        "body": "Failed to connect to db",
                        "severity": {"text": "ERROR", "number": 17},
                        "resource": {"service.name": "frontend-proxy"},
                        "@timestamp": "2026-08-21T20:09:56.480Z"
                    }
                }
            ]
        }
    }
    mock_response.raise_for_status = MagicMock()
    mock_client.post.return_value = mock_response

    provider = OpenSearchProvider(client=mock_client)
    
    start_time = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 8, 21, 20, 15, tzinfo=timezone.utc)

    evidence = provider.search_logs("frontend", start_time, end_time)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.type == EvidenceType.LOG
    assert ev.source_provider == "opensearch"
    assert ev.service_resource == "frontend-proxy"
    assert ev.data["message"] == "Failed to connect to db"
    assert ev.data["level"] == "ERROR"
    assert ev.observed_at.year == 2026
    assert ev.observed_at.tzinfo is not None

def test_search_logs_empty() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "hits": {
            "hits": []
        }
    }
    mock_response.raise_for_status = MagicMock()
    mock_client.post.return_value = mock_response

    provider = OpenSearchProvider(client=mock_client)
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(minutes=5)

    evidence = provider.search_logs("missing", start_time, end_time)
    assert len(evidence) == 0

def test_search_logs_http_error() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500 Internal Server Error", request=MagicMock(), response=mock_response
    )
    mock_client.post.return_value = mock_response

    provider = OpenSearchProvider(client=mock_client)
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(minutes=5)

    with pytest.raises(RuntimeError, match="Failed to query OpenSearch"):
        provider.search_logs("test", start_time, end_time)

def test_search_logs_api_error() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "error": "Index missing"
    }
    mock_client.post.return_value = mock_response

    provider = OpenSearchProvider(client=mock_client)
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(minutes=5)

    with pytest.raises(RuntimeError, match="OpenSearch API error: Index missing"):
        provider.search_logs("test", start_time, end_time)
