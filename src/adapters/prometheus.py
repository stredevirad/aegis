import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pydantic import AwareDatetime

from src.domain import (
    Evidence,
    EvidenceType,
    HealthStatus,
    MetricsProvider,
    ProviderStatus,
    ServiceHealth,
)


class PrometheusProvider(MetricsProvider):
    """Adapter for querying metrics from a Prometheus backend."""

    MAX_QUERY_WINDOW_HOURS = 12

    def __init__(self, endpoint: str = "http://localhost:9090", client: httpx.Client | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.client = client or httpx.Client(timeout=10.0)

    def get_status(self) -> ProviderStatus:
        """Perform a lightweight health check against Prometheus."""
        try:
            response = self.client.get(f"{self.endpoint}/-/healthy")
            response.raise_for_status()
            return ProviderStatus(
                provider="prometheus",
                health=HealthStatus.HEALTHY,
                capabilities=["query_metrics", "range_queries"]
            )
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            return ProviderStatus(
                provider="prometheus",
                health=HealthStatus.UNAVAILABLE,
                capabilities=[],
                limitations=[f"Prometheus unreachable: {e!s}"]
            )

    def get_service_health(self, service_name: str) -> ServiceHealth:
        """
        Generic service health inference from Prometheus is non-trivial without
        hardcoding specific alert names or metrics. For Phase 2B, we return UNKNOWN.
        """
        return ServiceHealth(
            service_id=service_name,
            status=HealthStatus.UNKNOWN,
            summary="Service health inference not yet implemented for generic Prometheus.",
            observed_at=datetime.now(timezone.utc)
        )

    def query_metrics(
        self, query: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        """
        Query Prometheus via the /api/v1/query_range endpoint using PromQL.
        Validates time bounds and normalizes the result into Evidence.
        """
        # Translate generic queries to PromQL if they don't look like PromQL
        if "{" not in query and "(" not in query and "[" not in query:
            # Assume it is a generic service name or keyword
            # e.g., 'frontend' -> http_server_request_duration_seconds_count{service_name=~".*frontend.*"}
            query = f'http_server_request_duration_seconds_count{{service_name=~".*{query}.*"}}'
            # Also fall back to up metric if preferred, but http_server_request_duration_seconds_count is good for OTel demo
        
        # Time bound validation
        if end_time < start_time:
            raise ValueError("end_time must be greater than or equal to start_time")
        
        window = end_time - start_time
        if window > timedelta(hours=self.MAX_QUERY_WINDOW_HOURS):
            raise ValueError(f"Query window exceeds maximum allowed ({self.MAX_QUERY_WINDOW_HOURS} hours)")

        # We calculate a reasonable step for the range query (e.g., 60 seconds or based on window)
        step = max(15, int(window.total_seconds() / 100))  # At most 100 points, min 15s

        params: dict[str, str | int | float] = {
            "query": query,
            "start": start_time.timestamp(),
            "end": end_time.timestamp(),
            "step": step,
        }

        try:
            response = self.client.get(f"{self.endpoint}/api/v1/query_range", params=params)
            response.raise_for_status()
            
            try:
                payload = response.json()
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Prometheus returned invalid JSON: {e!s}") from e

            if payload.get("status") != "success":
                raise RuntimeError(f"Prometheus API error: {payload.get('error')}")

            data = payload.get("data", {})
            results = data.get("result", [])
            
            return self._normalize_results(query, results)

        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            raise RuntimeError(f"Failed to query Prometheus: {e!s}") from e

    def _normalize_results(self, query: str, results: list[dict[str, Any]]) -> list[Evidence]:
        """Converts Prometheus JSON result array into Evidence objects."""
        evidence_list = []
        collected_at = datetime.now(timezone.utc)

        for result in results:
            metric = result.get("metric", {})
            values = result.get("values", [])
            
            # Map typical OTel service identifiers
            service_resource = metric.get("service_name") or metric.get("job")

            # Determine the most recent timestamp in this series as observed_at
            observed_ts = collected_at.timestamp()
            if values:
                observed_ts = float(values[-1][0])
            observed_at = datetime.fromtimestamp(observed_ts, tz=timezone.utc)

            # Generate a stable ID based on query and metric labels
            label_str = ",".join(f"{k}={v}" for k, v in sorted(metric.items()))
            signature = f"{query}::{label_str}::{observed_ts}"
            evidence_id = f"ev_prom_{hashlib.md5(signature.encode()).hexdigest()[:12]}"

            metric_name = metric.get("__name__", "unknown_metric")

            evidence_list.append(
                Evidence(
                    id=evidence_id,
                    type=EvidenceType.METRIC,
                    source_provider="prometheus",
                    service_resource=service_resource,
                    summary=f"Prometheus metric: {metric_name}",
                    data={
                        "query": query,
                        "metric": metric,
                        "values": values,
                    },
                    observed_at=observed_at,
                    collected_at=collected_at,
                )
            )

        return evidence_list
