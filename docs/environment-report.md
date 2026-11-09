> **Research-era document (2026-08)**, kept as a historical record of the research prototype (`src/`). The current product is described in [README.md](../README.md).

# Environment Report

## Host System
* **OS**: Windows 11-class (Version 2009, Build 26100)
* **CPU**: Intel(R) Core(TM) Ultra 5 125H
* **RAM**: ~16.7 GB (16766066688 bytes)
* **WSL2**: Default distro is docker-desktop (WSL 2)

## Developer Tools
* **Docker**: version 28.3.3
* **Docker Compose**: version v2.39.2-desktop.1
* **Git**: version 2.51.0.windows.1

## AI Models (Ollama)
* **API Availability**: Verified at http://localhost:11434
* **Local Models**: 
  * `qwen3.5:9b` (6.6 GB) - Target model
  * `llama3:latest` (4.7 GB)
  * `llama3.2:1b` (1.3 GB)
* **Capabilities for qwen3.5**: vision, completion, tools, thinking

## Workspace
* **Location**: `C:\Users\LENOVO\Desktop\Aegis`
* **Network/Ports**: Local loopback available for API servers.

## OpenTelemetry Demo (Fixture)
* **Repository**: Cloned `https://github.com/open-telemetry/opentelemetry-demo` locally.
* **Architecture**: The repository supports minimal deployments via `compose.yaml` and separated observability backends via `compose.observability.yaml`.
