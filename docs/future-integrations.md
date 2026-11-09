> **Research-era document (2026-08)**, kept as a historical record of the research prototype (`src/`). The current product is described in [README.md](../README.md).

# Future Integrations

Aegis is designed to decouple the investigation logic from the deployment environments and telemetry backends. The following are architectural possibilities without requiring core engine changes:

## Telemetry Integrations
* **Metrics**: Prometheus (planned), Datadog, CloudWatch.
* **Logs**: Loki (planned), ElasticSearch, Splunk.
* **Traces**: Tempo/Jaeger (planned), Honeycomb, AWS X-Ray.

## Environment Integrations
* `DockerEnvironmentProvider`: Local container inspections.
* `KubernetesProvider`: K8s API integration for pod health, events, and deployments.
* `AWSProvider` / `GCPProvider` / `AzureProvider`: Cloud-native architecture topology mapping.

## Multimodal Features
* `ImageEvidence`: A planned schema for monitoring screenshots or terminal outputs. 

## LLM Integrations
* `GeminiProvider`, `ClaudeProvider`, `OpenAIProvider`: For comparative research analysis against the local `OllamaProvider`. 

**Note**: V1 focuses on standard OpenTelemetry stacks, local Docker, and Ollama. Other integrations belong in future milestones.
