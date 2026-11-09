> **Research-era document (2026-08)**, kept as a historical record of the research prototype (`src/`). The current product is described in [README.md](../README.md).

# Troubleshooting Guide

## Docker Issues
* **OOM Errors**: Ensure Docker Desktop memory limits are appropriate for a 16GB system (recommend ~8-10GB for Docker). If running the full OpenTelemetry demo, consider trimming unused services (e.g., Kafka, frontend if testing backend).
* **Port Conflicts**: Common ports (8080, 4317, 9090, 3100) might conflict with local services. Always check port availability (`netstat -ano`) before starting environments.

## Ollama Issues
* **Model Not Found**: Ensure `qwen3.5:4b` (or `qwen3.5:9b`) is installed via `ollama run qwen3.5:4b`.
* **API Timeout**: If the local API (`http://localhost:11434`) times out, restart the Ollama service.
* **Slow Inference**: Check `OLLAMA_THINK` flag. Set to `false` for standard development. 

## Environment Mismatches
* Validate `.env` against `.env.example`.
* Ensure Python dependencies (when added) match the expected environment.
