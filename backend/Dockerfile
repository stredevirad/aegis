# syntax=docker/dockerfile:1
# Aegis backend: one image for the API, the worker and the one-shot operator commands
# (bootstrap-db, migrate, admin create-org / create-api-key / seed-demo).
#
# The synthetic scenarios live outside this build context; pass them as a named context:
#   docker build --build-context scenarios=eval/scenarios -t aegis-backend:dev backend
# (deploy/compose.yaml does this with additional_contexts).

FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM python:3.15.0-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Dependencies first (cached while only application code changes).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY aegis ./aegis
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.15.0-slim AS runtime
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AEGIS_SCENARIO_DIR=/app/scenarios
RUN groupadd --system --gid 10001 aegis \
    && useradd --system --uid 10001 --gid aegis --home-dir /app --no-create-home aegis \
    && mkdir -p /run/aegis \
    && chown aegis:aegis /run/aegis
WORKDIR /app
# Same path as the build stage: the console scripts' shebangs point at /app/.venv/bin/python.
COPY --from=build /app/.venv /app/.venv
COPY --from=scenarios . /app/scenarios
USER 10001:10001
EXPOSE 8000
# No image-level HEALTHCHECK: the worker serves no HTTP. Each compose service defines its own.
CMD ["aegis", "api", "--host", "0.0.0.0", "--port", "8000"]
