"""Application factory.

``create_app`` refuses to build an application whose configuration is unsafe for its profile
(security model C21): every violation is listed and startup fails.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aegis import __version__
from aegis.api import errors
from aegis.api.middleware import RequestContextMiddleware
from aegis.api.routers import admin, auth, incidents, ingest, investigations
from aegis.api.state import AppState
from aegis.auth.oidc import OidcClient
from aegis.config import Settings, validate_for_profile
from aegis.db.engine import make_engine
from aegis.investigations.sources import ScenarioRegistry


def migration_head() -> str | None:
    """The newest migration revision shipped with this build."""
    from alembic.script import ScriptDirectory

    from aegis.db.migrate import alembic_config

    return ScriptDirectory.from_config(alembic_config()).get_current_head()


class UnsafeConfiguration(RuntimeError):
    pass


def create_app(
    settings: Settings,
    engine: AsyncEngine | None = None,
    scenarios: ScenarioRegistry | None = None,
    oidc_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    problems = validate_for_profile(settings)
    if problems:
        raise UnsafeConfiguration(
            f"refusing to start with an unsafe {settings.profile.value} configuration: "
            + "; ".join(problems)
        )
    expected_migration = migration_head()
    owns_engine = engine is None
    db = engine or make_engine(settings)
    state = AppState(
        settings=settings, engine=db, scenarios=scenarios or ScenarioRegistry(settings.scenario_dir)
    )
    if settings.auth_mode == "oidc":
        if not (settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret):
            raise UnsafeConfiguration(
                "auth_mode=oidc requires oidc_issuer, oidc_client_id and oidc_client_secret"
            )
        state.oidc = OidcClient(settings, transport=oidc_transport)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_engine:
            await db.dispose()

    app = FastAPI(
        title="Aegis API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.dev_like else None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    app.state.aegis = state
    errors.install(app)
    app.add_middleware(RequestContextMiddleware, max_body_bytes=settings.max_body_bytes)

    v1 = APIRouter(prefix="/api/v1")
    for r in (incidents.router, investigations.router, ingest.router, admin.router):
        v1.include_router(r)
    app.include_router(v1)
    app.include_router(auth.router)
    if settings.dev_like and settings.auth_mode == "dev":
        app.include_router(auth.dev_router)
    if settings.auth_mode == "oidc":
        app.include_router(auth.oidc_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """Liveness: the process is up and serving HTTP. Does not touch dependencies."""
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> Any:
        """Readiness: the database is reachable and migrated to the version this build expects."""
        try:
            async with db.connect() as c:
                version = (
                    await c.execute(text("SELECT version_num FROM aegis.alembic_version"))
                ).scalar_one_or_none()
        except Exception:
            return JSONResponse(
                {"status": "unavailable", "database": "unreachable"}, status_code=503
            )
        if version != expected_migration:
            return JSONResponse(
                {
                    "status": "unavailable",
                    "database": "ok",
                    "migration": version,
                    "expected": expected_migration,
                },
                status_code=503,
            )
        return {"status": "ready", "database": "ok", "migration": version}

    return app
