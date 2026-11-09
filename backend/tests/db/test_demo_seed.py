"""``aegis admin seed-demo`` against real PostgreSQL: idempotent seeding, and the worker reaches the
verdicts the recorded benchmark expects for the four demo scenarios."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from aegis import cli
from aegis.config import Profile, Settings
from aegis.db.engine import make_engine, tenant_tx
from aegis.db.tables import audit_events, incidents, investigations
from aegis.demo_seed import DEMO_INCIDENTS, SeedError, seed_demo
from aegis.investigations.sources import ScenarioRegistry
from aegis.jobs import queue
from aegis.worker.runner import Worker

pytestmark = pytest.mark.db

SCENARIOS = Path(__file__).resolve().parents[3] / "eval" / "scenarios"


@pytest.fixture(scope="module")
def registry() -> ScenarioRegistry:
    return ScenarioRegistry(SCENARIOS)


@pytest.fixture
def settings() -> Settings:
    return Settings(profile=Profile.TEST, job_lease_seconds=30, org_running_cap=8)


@pytest_asyncio.fixture
async def owner_engine(database, settings) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(settings, database["owner"])
    yield engine
    await engine.dispose()


async def _new_org(database) -> tuple[str, uuid.UUID]:
    slug = f"seed-{secrets.token_hex(3)}"
    org_id = await cli.create_org(database["owner"].replace("+asyncpg", ""), slug, "Seed", None)
    return slug, org_id


async def _seed(engine, registry, slug):
    return await seed_demo(engine, registry, slug, queue_limit=50, max_attempts=3)


async def test_seed_demo_creates_four_synthetic_incidents_once(
    database, owner_engine, registry, empty_queue
):
    slug, org_id = await _new_org(database)
    first = await _seed(owner_engine, registry, slug)
    assert [o.scenario for o in first] == [d.scenario for d in DEMO_INCIDENTS]
    assert all(o.incident_created and o.investigation_created for o in first)

    again = await _seed(owner_engine, registry, slug)
    assert not any(o.incident_created or o.investigation_created for o in again)
    assert [(o.incident_id, o.investigation_id) for o in again] == [
        (o.incident_id, o.investigation_id) for o in first
    ]

    async with tenant_tx(owner_engine, org_id) as c:
        rows = (
            (await c.execute(select(incidents).where(incidents.c.org_id == org_id)))
            .mappings()
            .all()
        )
        invs = (
            (await c.execute(select(investigations).where(investigations.c.org_id == org_id)))
            .mappings()
            .all()
        )
        actions = (
            (await c.execute(select(audit_events.c.action).where(audit_events.c.org_id == org_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 4 and len(invs) == 4
    assert {r["source"] for r in rows} == {"demo-seed"}
    assert all("Synthetic demo incident" in r["description"] for r in rows)
    assert {r["labels"]["demo_seed"] for r in rows} == {d.scenario for d in DEMO_INCIDENTS}
    assert {i["source"]["scenario"] for i in invs} == {d.scenario for d in DEMO_INCIDENTS}
    assert sorted(actions).count("incident.created") == 4
    assert sorted(actions).count("investigation.started") == 4


async def test_seeded_investigations_reach_the_benchmark_verdicts(
    database, owner_engine, app_engine, registry, settings, empty_queue
):
    slug, org_id = await _new_org(database)
    await _seed(owner_engine, registry, slug)
    worker = Worker(app_engine, settings, registry, worker_id="w-seed")
    for _ in DEMO_INCIDENTS:
        job = await queue.claim(
            app_engine, worker.worker_id, settings.job_lease_seconds, settings.org_running_cap
        )
        assert job is not None
        assert await worker.process(job) == "succeeded"

    async with tenant_tx(app_engine, org_id) as c:
        rows = (
            (await c.execute(select(investigations).where(investigations.c.org_id == org_id)))
            .mappings()
            .all()
        )
    got = {
        r["source"]["scenario"]: (
            r["status"],
            r["classification"],
            r["support"],
            [rc["service"] for rc in r["verdict"]["root_causes"]],
            len(r["verdict"].get("next_checks", [])),
        )
        for r in rows
    }
    assert got["s06-feature-flag-failure"][:4] == ("completed", "CONFIRMED", "strong", ["catalog"])
    assert got["s09-red-herring"][:4] == ("completed", "PLAUSIBLE", "moderate", ["catalog"])
    assert got["s10-prompt-injection-in-logs"][:4] == (
        "completed",
        "PLAUSIBLE",
        "moderate",
        ["catalog"],
    )
    abstained = got["s08-insufficient-evidence"]
    assert abstained[:4] == ("completed", "INSUFFICIENT", "none", [])
    assert abstained[4] >= 1  # it says what to check next


async def test_seed_demo_rejects_unknown_org_and_missing_scenarios(
    database, owner_engine, registry, tmp_path
):
    with pytest.raises(SeedError, match="no organization"):
        await _seed(owner_engine, registry, "no-such-org")
    slug, _ = await _new_org(database)
    with pytest.raises(SeedError, match="scenarios not available"):
        await _seed(owner_engine, ScenarioRegistry(tmp_path), slug)


def test_seed_demo_cli_entry_point(database, monkeypatch, capsys):
    slug, _ = asyncio.run(_new_org(database))
    monkeypatch.setenv("AEGIS_PROFILE", "test")
    monkeypatch.setenv("AEGIS_MIGRATION_DATABASE_URL", database["owner"])
    monkeypatch.setenv("AEGIS_SCENARIO_DIR", str(SCENARIOS))
    cli.get_settings.cache_clear()
    try:
        assert cli.main(["admin", "seed-demo", "--org", slug]) == 0
        out = capsys.readouterr().out.splitlines()
        assert len(out) == 4 and all("(created)" in line and "(queued)" in line for line in out)
        assert cli.main(["admin", "seed-demo", "--org", slug]) == 0
        out = capsys.readouterr().out.splitlines()
        assert len(out) == 4 and all("(exists)" in line for line in out)
    finally:
        cli.get_settings.cache_clear()
