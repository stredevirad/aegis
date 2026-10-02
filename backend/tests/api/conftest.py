"""API test fixtures: the real app over ASGI, real PostgreSQL as aegis_app, dev login."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from aegis.api.app import create_app
from aegis.config import Profile, Settings
from aegis.investigations.sources import ScenarioRegistry
from aegis.jobs import queue
from aegis.worker.runner import Worker
from tests.conftest import Tenant

SCENARIOS = Path(__file__).resolve().parents[3] / "eval" / "scenarios"
BASE = "http://testserver"
CSRF = {"X-Aegis-CSRF": "1", "Origin": BASE}


@pytest.fixture(scope="session")
def registry() -> ScenarioRegistry:
    return ScenarioRegistry(SCENARIOS)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        profile=Profile.TEST,
        auth_mode="dev",
        public_base_url=BASE,
        allowed_origins=[BASE],
        scenario_dir=SCENARIOS,
        org_queue_limit=20,
        job_heartbeat_seconds=1,
        ingest_per_minute=6000,
        ingest_burst=1000,
    )


@pytest.fixture
def app(settings, app_engine, registry):
    return create_app(settings, engine=app_engine, scenarios=registry)


@pytest_asyncio.fixture
async def member_factory(su_conn):
    async def make(tenant: Tenant, role: str, email: str | None = None) -> str:
        email = email or f"{role}-{secrets.token_hex(3)}@example.com"
        user_id = await su_conn.fetchval(
            "INSERT INTO aegis.users (issuer, subject, email, email_verified) VALUES ('aegis-dev', $1, $1, true) "
            "ON CONFLICT (issuer, subject) DO UPDATE SET email = EXCLUDED.email RETURNING id",
            email,
        )
        await su_conn.execute(
            "INSERT INTO aegis.memberships (org_id, user_id, role) VALUES ($1, $2, $3)",
            tenant.org_id,
            user_id,
            role,
        )
        return email

    return make


@pytest_asyncio.fixture
async def client_factory(app) -> AsyncIterator:
    clients: list[httpx.AsyncClient] = []

    async def make(email: str | None = None, org: str | None = None) -> httpx.AsyncClient:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
        clients.append(c)
        if email:
            r = await c.post("/auth/dev/login", json={"email": email, "org": org}, headers=CSRF)
            assert r.status_code == 200, r.text
        return c

    yield make
    for c in clients:
        await c.aclose()


async def make_key(admin: httpx.AsyncClient, *scopes: str) -> str:
    r = await admin.post(
        "/api/v1/api-keys", json={"name": "k", "scopes": list(scopes)}, headers=CSRF
    )
    assert r.status_code == 201, r.text
    return r.json()["secret"]


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def run_worker_once(app_engine, settings, registry) -> str | None:
    worker = Worker(app_engine, settings, registry, worker_id=f"test-{secrets.token_hex(3)}")
    job = await queue.claim(
        app_engine, worker.worker_id, settings.job_lease_seconds, settings.org_running_cap
    )
    if job is None:
        return None
    return await worker.process(job)


def alertmanager_payload(
    service: str = "checkout", fingerprint: str = "fp-1", status: str = "firing", **labels
):
    return {
        "version": "4",
        "status": status,
        "receiver": "aegis",
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": "HighErrorRate",
                    "service": service,
                    "severity": "critical",
                    **labels,
                },
                "annotations": {"summary": f"Error rate high on {service}"},
                "startsAt": "2026-10-02T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=x",
                "fingerprint": fingerprint,
            }
        ],
    }
