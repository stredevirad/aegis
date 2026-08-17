from abc import ABC, abstractmethod

from pydantic import AwareDatetime

from .llm_models import LLMRequest, LLMResponse
from .models import Evidence, Incident, ProviderStatus, RemediationRecommendation, ServiceHealth


class BaseProvider(ABC):
    @abstractmethod
    def get_status(self) -> ProviderStatus:
        pass


class MetricsProvider(BaseProvider):
    @abstractmethod
    def get_service_health(self, service_name: str) -> ServiceHealth:
        pass

    @abstractmethod
    def query_metrics(
        self, query: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        """Query metrics from the backend.

        Note: ``query`` is a backend-specific pass-through string (e.g.
        PromQL for Prometheus, metric namespace for CloudWatch).  The
        investigation engine's tool layer is responsible for constructing
        the query appropriate to the registered provider.
        """


class LogsProvider(BaseProvider):
    @abstractmethod
    def search_logs(
        self, query: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        """Search logs from the backend.

        Note: ``query`` is a backend-specific pass-through string (e.g.
        LogQL for Loki, CloudWatch Insights syntax for AWS).  The
        investigation engine's tool layer is responsible for constructing
        the query appropriate to the registered provider.
        """


class TracesProvider(BaseProvider):
    @abstractmethod
    def get_trace(self, trace_id: str) -> Evidence | None:
        pass

    @abstractmethod
    def get_related_traces(
        self, service_name: str, start_time: AwareDatetime, end_time: AwareDatetime
    ) -> list[Evidence]:
        pass


class TopologyProvider(BaseProvider):
    @abstractmethod
    def get_service_dependencies(self, service_name: str) -> dict[str, list[str]]:
        pass


class EnvironmentProvider(BaseProvider):
    @abstractmethod
    def get_deployment_metadata(self, service_name: str) -> Evidence | None:
        pass


class LLMProvider(BaseProvider):
    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        pass


class RemediationProvider(BaseProvider):
    @abstractmethod
    def recommend_action(self, incident: Incident) -> RemediationRecommendation:
        """Produce a remediation recommendation for the given incident.

        V1 is recommendation-only.  This method MUST NOT execute
        operational changes (restarts, rollbacks, scaling, config
        mutations).  Execution will be gated behind Human Approval in a
        future version.
        """
