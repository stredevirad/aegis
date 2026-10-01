# Title: Product package and one-way import boundary
# Status: Accepted
# Date: 2026-10-01
# Source: production-readiness audit; Council A (docs/councils/council-A-architecture.md)

## Context
The product needs different dependencies (FastAPI, SQLAlchemy, asyncpg, OpenTelemetry) and different guarantees from the research engine.

## Decision
The product is the Python package `aegis` in `backend/`, distribution `aegis-platform`, with its own `pyproject.toml` and lock file. It must not import `src`; a test walks every module's imports and fails on violations. Research code may import `aegis.core` and `aegis.benchmark`. Knowledge from research adapters and the summarizer is **ported** (rewritten with fixes and tests), not imported.

## Consequences
Some duplication of concepts (Evidence, providers). Accepted: the product's versions are tenant-aware, async, status-preserving and safe.
