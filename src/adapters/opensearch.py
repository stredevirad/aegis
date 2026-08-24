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
    LogsProvider,
    ProviderStatus,
)


class OpenSearchProvider(LogsProvider):
    """Adapter for querying logs from an OpenSearch backend."""

    MAX_QUERY_WINDOW_HOURS = 12

    def __init__(self, endpoint: str = "http://localhost:9200", client: httpx.Client | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.client = client or httpx.Client(timeout=10.0)

    def get_status(self) -> ProviderStatus:
        """Perform a lightweight health check against OpenSearch."""
        try:
            response = self.client.get(f"{self.endpoint}/_cluster/health")
            response.raise_for_status()
            # If we get a 200 OK from health, we consider it healthy regardless of yellow/green clustering
            return ProviderStatus(
                provider="opensearch",
                health=HealthStatus.HEALTHY,
                capabilities=["search_logs"]
            )
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            return ProviderStatus(
                provider="opensearch",
                health=HealthStatus.UNAVAILABLE,
                capabilities=[],
                limitations=[f"OpenSearch unreachable: {e!s}"]
            )

    def search_logs(
        self, query: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        """
        Query OpenSearch via /otel-logs-*/_search using a bool query.
        Validates time bounds and normalizes the result into Evidence.
        """
        # Time bound validation
        if end_time < start_time:
            raise ValueError("end_time must be greater than or equal to start_time")
        
        window = end_time - start_time
        if window > timedelta(hours=self.MAX_QUERY_WINDOW_HOURS):
            raise ValueError(f"Query window exceeds maximum allowed ({self.MAX_QUERY_WINDOW_HOURS} hours)")

        # Construct OpenSearch query body
        payload = {
            "size": 100,  # Bounded result size for safe local execution
            "query": {
                "bool": {
                    "must": [
                        {
                            "query_string": {
                                "query": query
                            }
                        }
                    ],
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": start_time.isoformat(),
                                    "lte": end_time.isoformat()
                                }
                            }
                        }
                    ]
                }
            },
            "sort": [
                {"@timestamp": {"order": "desc"}}
            ]
        }

        try:
            response = self.client.post(f"{self.endpoint}/otel-logs-*/_search", json=payload)
            response.raise_for_status()
            
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                raise RuntimeError(f"OpenSearch returned invalid JSON: {e!s}") from e

            # Check if there's an elasticsearch-level error returned as 200 with error property (rare but possible)
            if "error" in data:
                raise RuntimeError(f"OpenSearch API error: {data['error']}")

            hits = data.get("hits", {}).get("hits", [])
            
            return self._normalize_results(hits)

        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            raise RuntimeError(f"Failed to query OpenSearch: {e!s}") from e

    def _normalize_results(self, hits: list[dict[str, Any]]) -> list[Evidence]:
        """Converts OpenSearch hit documents into Evidence objects."""
        evidence_list = []
        collected_at = datetime.now(timezone.utc)

        for hit in hits:
            source = hit.get("_source", {})
            index = hit.get("_index", "unknown")
            doc_id = hit.get("_id", "unknown")
            
            # Map typical OTel log identifiers
            # Try nested resource dict or flattened resource.service.name
            resource = source.get("resource", {})
            service_resource = resource.get("service.name") or source.get("service_name") or source.get("service.name", "unknown")

            # Extract message
            message = source.get("body", "")
            
            # Extract severity/level
            severity = source.get("severity", {})
            level = severity.get("text", "UNKNOWN").upper()
            if not isinstance(severity, dict):
                level = str(severity).upper()
            
            # Extract observed timestamp
            timestamp_str = source.get("@timestamp") or source.get("observedTimestamp")
            try:
                # Naively parse ISO format - if missing or invalid, fall back to collected time
                if timestamp_str:
                    # Replace Z with +00:00 for python fromisoformat
                    timestamp_str = timestamp_str.replace("Z", "+00:00")
                    observed_at = datetime.fromisoformat(timestamp_str)
                else:
                    observed_at = collected_at
            except (ValueError, TypeError):
                observed_at = collected_at

            # Ensure timezone awareness
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)

            # Generate a stable ID based on index and doc ID
            signature = f"{index}::{doc_id}"
            evidence_id = f"ev_log_{hashlib.md5(signature.encode()).hexdigest()[:12]}"

            # Create a compact summary for the LLM context
            truncated_msg = message[:100] + "..." if len(message) > 100 else message
            summary = f"[{level}] {truncated_msg}".strip()

            evidence_list.append(
                Evidence(
                    id=evidence_id,
                    type=EvidenceType.LOG,
                    source_provider="opensearch",
                    service_resource=service_resource,
                    summary=summary,
                    data={
                        "message": message,
                        "level": level,
                        "attributes": source.get("attributes", {}),
                        "document_id": doc_id,
                        "index": index
                    },
                    observed_at=observed_at,
                    collected_at=collected_at,
                )
            )

        return evidence_list
