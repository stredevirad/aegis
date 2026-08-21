from datetime import datetime, timezone

from pydantic import AwareDatetime

from src.domain import (
    Evidence,
    EvidenceType,
    HealthStatus,
    LogsProvider,
    MetricsProvider,
    ProviderStatus,
    ServiceHealth,
    TopologyProvider,
)


class MockMetricsProvider(MetricsProvider):
    def get_status(self) -> ProviderStatus:
        return ProviderStatus(provider="mock-metrics", health=HealthStatus.HEALTHY)

    def get_service_health(self, service_name: str) -> ServiceHealth:
        status = HealthStatus.HEALTHY
        if service_name == "payment-api" or service_name == "downstream-db":
            status = HealthStatus.DEGRADED

        return ServiceHealth(
            service_id=service_name, status=status, observed_at=datetime.now(timezone.utc)
        )

    def query_metrics(
        self, query: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        # Fake logic based on the generic query terms provided by the engine
        now = datetime.now(timezone.utc)
        evidence = []
        if "payment-api" in query and "latency" in query:
            evidence.append(
                Evidence(
                    id="ev_metric_1",
                    type=EvidenceType.METRIC,
                    source_provider="mock-metrics",
                    service_resource="payment-api",
                    summary="Elevated latency on payment-api",
                    data={
                        "metric_name": "http_request_duration_ms",
                        "value": 1500.0,
                        "unit": "ms",
                        "threshold": 500.0,
                    },
                    observed_at=now,
                    collected_at=now,
                )
            )
        elif "downstream-db" in query and "latency" in query:
            evidence.append(
                Evidence(
                    id="ev_metric_2",
                    type=EvidenceType.METRIC,
                    source_provider="mock-metrics",
                    service_resource="downstream-db",
                    summary="Severe latency spike on downstream-db",
                    data={
                        "metric_name": "db_query_duration_ms",
                        "value": 4500.0,
                        "unit": "ms",
                        "threshold": 100.0,
                    },
                    observed_at=now,
                    collected_at=now,
                )
            )
        elif "unrelated-service" in query:
            evidence.append(
                Evidence(
                    id="ev_metric_3",
                    type=EvidenceType.METRIC,
                    source_provider="mock-metrics",
                    service_resource="unrelated-service",
                    summary="Normal latency on unrelated-service",
                    data={
                        "metric_name": "http_request_duration_ms",
                        "value": 45.0,
                        "unit": "ms",
                        "threshold": 500.0,
                    },
                    observed_at=now,
                    collected_at=now,
                )
            )
        elif "orders-service" in query:
            evidence.append(
                Evidence(
                    id="ev_metric_orders",
                    type=EvidenceType.METRIC,
                    source_provider="mock-metrics",
                    service_resource="orders-service",
                    summary="High latency on orders-service",
                    data={
                        "metric_name": "http_request_duration_ms",
                        "value": 2000.0,
                        "unit": "ms",
                        "threshold": 500.0,
                    },
                    observed_at=now,
                    collected_at=now,
                )
            )
        return evidence


class MockLogsProvider(LogsProvider):
    def get_status(self) -> ProviderStatus:
        return ProviderStatus(provider="mock-logs", health=HealthStatus.HEALTHY)

    def search_logs(
        self, query: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        now = datetime.now(timezone.utc)
        evidence = []
        if "payment-api" in query and ("error" in query or "timeout" in query):
            evidence.append(
                Evidence(
                    id="ev_log_1",
                    type=EvidenceType.LOG,
                    source_provider="mock-logs",
                    service_resource="payment-api",
                    summary="Timeout errors connecting to downstream-db",
                    data={
                        "message": "Timeout waiting for response from downstream-db",
                        "level": "ERROR",
                    },
                    observed_at=now,
                    collected_at=now,
                )
            )
        elif "orders-service" in query:
            evidence.append(
                Evidence(
                    id="ev_log_orders",
                    type=EvidenceType.LOG,
                    source_provider="mock-logs",
                    service_resource="orders-service",
                    summary="Timeout in orders-service",
                    data={
                        "message": "Timeout waiting for inventory",
                        "level": "ERROR",
                    },
                    observed_at=now,
                    collected_at=now,
                )
            )
        return evidence


class MockTopologyProvider(TopologyProvider):
    def get_status(self) -> ProviderStatus:
        return ProviderStatus(provider="mock-topology", health=HealthStatus.HEALTHY)

    def get_service_dependencies(self, service_name: str) -> dict[str, list[str]]:
        topology = {
            "frontend": ["payment-api", "cart-api"],
            "payment-api": ["downstream-db", "auth-service"],
            "cart-api": ["downstream-db"],
        }
        return {service_name: topology.get(service_name, [])}
