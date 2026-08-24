from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from src.adapters.prometheus import PrometheusProvider
from src.domain import EvidenceType, HealthStatus


def test_prometheus_health_healthy() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client.get.return_value = mock_response

    provider = PrometheusProvider(client=mock_client)
    status = provider.get_status()

    assert status.health == HealthStatus.HEALTHY
    assert "query_metrics" in status.capabilities

def test_prometheus_health_unavailable() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = httpx.RequestError("Connection Refused")

    provider = PrometheusProvider(client=mock_client)
    status = provider.get_status()

    assert status.health == HealthStatus.UNAVAILABLE
    assert "Prometheus unreachable" in status.limitations[0]

def test_query_metrics_bounds_reversed() -> None:
    provider = PrometheusProvider()
    start_time = datetime.now(timezone.utc)
    end_time = start_time - timedelta(minutes=5)

    with pytest.raises(ValueError, match="must be greater than or equal to start_time"):
        provider.query_metrics("test_query", start_time, end_time)

def test_query_metrics_bounds_exceeded() -> None:
    provider = PrometheusProvider()
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(hours=13)

    with pytest.raises(ValueError, match="exceeds maximum allowed"):
        provider.query_metrics("test_query", start_time, end_time)

def test_query_metrics_success() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {
                        "__name__": "http_server_duration",
                        "service_name": "payment-api",
                        "job": "payment"
                    },
                    "values": [
                        [1700000000, "0.5"],
                        [1700000060, "0.6"]
                    ]
                }
            ]
        }
    }
    mock_response.raise_for_status = MagicMock()
    mock_client.get.return_value = mock_response

    provider = PrometheusProvider(client=mock_client)
    
    start_time = datetime(2023, 11, 14, 12, 0, tzinfo=timezone.utc)
    end_time = datetime(2023, 11, 14, 12, 15, tzinfo=timezone.utc)

    evidence = provider.query_metrics("test_query", start_time, end_time)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.type == EvidenceType.METRIC
    assert ev.source_provider == "prometheus"
    assert ev.service_resource == "payment-api"
    assert "test_query" in ev.data["query"]
    assert "values" in ev.data
    # Check observed_at is derived from last timestamp (1700000060)
    assert ev.observed_at.timestamp() == 1700000060

def test_query_metrics_http_error() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "400 Bad Request", request=MagicMock(), response=mock_response
    )
    mock_client.get.return_value = mock_response

    provider = PrometheusProvider(client=mock_client)
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(minutes=5)

    with pytest.raises(RuntimeError, match="Failed to query Prometheus"):
        provider.query_metrics("bad_query", start_time, end_time)

def test_query_metrics_api_error() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "status": "error",
        "errorType": "bad_data",
        "error": "invalid parameter 'query'"
    }
    mock_client.get.return_value = mock_response

    provider = PrometheusProvider(client=mock_client)
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(minutes=5)

    with pytest.raises(RuntimeError, match="Prometheus API error"):
        provider.query_metrics("bad_query", start_time, end_time)
