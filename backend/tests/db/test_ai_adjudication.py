"""AI adjudication through the worker (fake provider; real PostgreSQL, policy, slots, records)."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy import insert, select, text

from aegis.config import Profile, Settings
from aegis.db.engine import tenant_tx
from aegis.db.tables import audit_events, integrations, investigations, llm_calls
from aegis.integrations.registry import Connector, ConnectorKind, Registry
from aegis.investigations.sources import ScenarioRegistry
from aegis.jobs import queue
from aegis.llm.adjudicate import Adjudicator
from aegis.llm.providers import ModelResult, ProviderError
from aegis.security.crypto import Keyring
from aegis.worker.runner import Worker

pytestmark = pytest.mark.db

SCENARIOS = Path(__file__).resolve().parents[3] / "eval" / "scenarios"
REGISTRY = ScenarioRegistry(SCENARIOS)


class FakeProvider:
    """Plays back scripted outputs (str JSON or ProviderError) and records what it was sent."""

    script: ClassVar[list] = []
    seen: ClassVar[list] = []

    def __init__(self, connector, credential, egress):
        self.connector = connector

    async def complete(self, call):
        FakeProvider.seen.append(call)
        item = FakeProvider.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return ModelResult(item, 1000, 200, 5)


def good_output(
    inv_analysis_root: str,
    evidence_ids: list[str],
    classification="CONFIRMED",
    support="strong",
    mechanism="change_induced:deployment",
) -> str:
    return json.dumps(
        {
            "selected_candidate": inv_analysis_root,
            "classification": classification,
            "support": support,
            "mechanism": mechanism,
            "claims": [
                {
                    "type": "observation",
                    "text": "deploy preceded the error spike",
                    "evidence_ids": evidence_ids[:2],
                }
            ],
            "contradicting_evidence_ids": [],
            "uncertainty": "single deployment observed",
            "missing_information": [],
            "summary": "The catalog deployment explains the errors.",
            "next_check": None,
        }
    )


async def setup(
    su_conn, app_engine, *, locality="local", ai=True, allow_cloud=False, budget=200_000
):
    slug = f"ai-{secrets.token_hex(3)}"
    org = await su_conn.fetchval(
        "INSERT INTO aegis.organizations (slug, name) VALUES ($1, $1) RETURNING id", slug
    )
    conn = Connector(
        id="llm-local" if locality == "local" else "llm-cloud",
        kind=ConnectorKind.LLM,
        url="http://llm.test:11434",
        orgs=[slug],
        options={"api": "ollama", "locality": locality, "default_model": "qwen-test"},
    )
    async with tenant_tx(app_engine, org) as c:
        integ = (
            await c.execute(
                insert(integrations)
                .values(org_id=org, connector_id=conn.id, kind="llm", name="m")
                .returning(integrations.c.id)
            )
        ).scalar_one()
    settings = {
        "ai_enabled": ai,
        "ai_integration_id": str(integ),
        "ai_allow_cloud": allow_cloud,
        "ai_daily_token_budget": budget,
        "default_source": {"kind": "scenario", "scenario": "s05-deployment-regression"},
    }
    await su_conn.execute(
        "UPDATE aegis.organizations SET settings = $1::jsonb WHERE id = $2",
        json.dumps(settings),
        org,
    )
    return org, Registry([conn])


async def run_one(app_engine, org, registry, settings=None):
    settings = settings or Settings(
        profile=Profile.TEST, job_heartbeat_seconds=1, llm_slot_wait_seconds=0
    )
    async with tenant_tx(app_engine, org) as c:
        inc = (
            await c.execute(
                text(
                    "INSERT INTO incidents (org_id, title, severity, started_at) VALUES (:o, 'x', 'high', now()) RETURNING id"
                ),
                {"o": org},
            )
        ).scalar_one()
        res = await queue.enqueue_investigation(
            c,
            org_id=org,
            incident_id=inc,
            windows=REGISTRY.windows("s05-deployment-regression"),
            source={"kind": "scenario", "scenario": "s05-deployment-regression"},
            trigger="manual",
            requested_by="t",
            idempotency_key=None,
            queue_limit=100,
            max_attempts=3,
        )
    adj = Adjudicator(
        app_engine,
        settings,
        registry,
        Keyring.from_settings(settings),
        egress=None,
        provider_factory=FakeProvider,
    )
    w = Worker(
        app_engine, settings, REGISTRY, worker_id=f"w-{secrets.token_hex(3)}", adjudicator=adj
    )
    outcome = await w.process(await queue.claim(app_engine, w.worker_id, 60, 4))
    async with tenant_tx(app_engine, org) as c:
        inv = (
            (
                await c.execute(
                    select(investigations).where(investigations.c.id == res.investigation_id)
                )
            )
            .mappings()
            .one()
        )
        calls = (
            (
                await c.execute(
                    select(llm_calls)
                    .where(llm_calls.c.investigation_id == res.investigation_id)
                    .order_by(llm_calls.c.started_at)
                )
            )
            .mappings()
            .all()
        )
        actions = [
            r[0]
            for r in await c.execute(
                select(audit_events.c.action).where(
                    audit_events.c.target_id == str(res.investigation_id)
                )
            )
        ]
    return outcome, inv, calls, actions


def _root(inv):
    rc = inv["verdict"]["root_causes"][0]
    return rc["service"], rc["supporting_evidence_ids"]


@pytest.fixture(autouse=True)
def _reset():
    FakeProvider.script, FakeProvider.seen = [], []


async def test_ai_is_off_by_default(app_engine, su_conn, empty_queue):
    org, registry = await setup(su_conn, app_engine, ai=False)
    outcome, inv, calls, _ = await run_one(app_engine, org, registry)
    assert outcome == "succeeded" and inv["verdict"]["ai"] == {"status": "disabled"}
    assert calls == [] and FakeProvider.seen == []


async def test_accepted_assessment_is_recorded_with_its_egress_record(
    app_engine, su_conn, empty_queue
):
    org, registry = await setup(su_conn, app_engine)
    # the deterministic answer for s05 is CONFIRMED/strong catalog; a faithful model agrees
    from tests.llm.test_validate import analysis

    a = analysis("s05-deployment-regression")
    FakeProvider.script = [good_output("catalog", a.verdict.root_causes[0].supporting_evidence_ids)]
    outcome, inv, calls, actions = await run_one(app_engine, org, registry)
    ai = inv["verdict"]["ai"]
    assert (
        outcome == "succeeded"
        and ai["status"] == "accepted"
        and ai["classification"] == "CONFIRMED"
    )
    assert (
        ai["provider"] == "llm-local" and ai["model"] == "qwen-test" and ai["locality"] == "local"
    )
    assert inv["classification"] == "CONFIRMED"  # deterministic columns unchanged
    [call] = calls
    assert (
        call["status"] == "ok" and call["attempt"] == "initial" and len(call["prompt_sha256"]) == 64
    )
    assert call["policy"]["locality"] == "local" and call["policy"]["cloud_opt_in"] is False
    assert {"ai.model_selected", "ai.output_accepted"} <= set(actions)
    sent = FakeProvider.seen[0]
    assert (
        sent.system.startswith("You are the adjudicator")
        and "AEGIS-DATA-" in sent.messages[0].content
    )


async def test_malformed_output_gets_one_repair(app_engine, su_conn, empty_queue):
    org, registry = await setup(su_conn, app_engine)
    from tests.llm.test_validate import analysis

    a = analysis("s05-deployment-regression")
    FakeProvider.script = [
        "DIAGNOSIS: catalog\nCLASSIFICATION: CONFIRMED",
        good_output("catalog", a.verdict.root_causes[0].supporting_evidence_ids),
    ]
    _, inv, calls, _ = await run_one(app_engine, org, registry)
    assert inv["verdict"]["ai"]["status"] == "accepted" and inv["verdict"]["ai"]["calls"] == 2
    assert [c["attempt"] for c in calls] == ["initial", "repair"] and [
        c["status"] for c in calls
    ] == ["invalid", "ok"]
    repair_turn = FakeProvider.seen[1].messages[-1].content
    assert "rejected by validation" in repair_turn


async def test_persistent_escalation_is_rejected_and_the_deterministic_verdict_stands(
    app_engine, su_conn, empty_queue
):
    org, registry = await setup(su_conn, app_engine)
    bad = good_output("ghost-service", ["ev-made-up"], mechanism="saturation")
    FakeProvider.script = [bad, bad]
    outcome, inv, calls, actions = await run_one(app_engine, org, registry)
    ai = inv["verdict"]["ai"]
    assert outcome == "succeeded" and inv["status"] == "completed" and ai["status"] == "invalid"
    assert any("not one of the analysis candidates" in e for e in ai["errors"])
    assert (
        inv["classification"] == "CONFIRMED"
        and inv["verdict"]["root_causes"][0]["service"] == "catalog"
    )
    assert [c["status"] for c in calls] == [
        "invalid",
        "invalid",
    ] and "ai.output_rejected" in actions


async def test_provider_down_falls_back_without_repair(app_engine, su_conn, empty_queue):
    org, registry = await setup(su_conn, app_engine)
    FakeProvider.script = [ProviderError("timeout", "model timed out")]
    outcome, inv, calls, _ = await run_one(app_engine, org, registry)
    assert outcome == "succeeded" and inv["verdict"]["ai"]["status"] == "unavailable"
    assert [c["status"] for c in calls] == ["timeout"] and len(FakeProvider.seen) == 1
    assert inv["classification"] == "CONFIRMED"


async def test_cloud_provider_without_opt_in_sends_nothing(app_engine, su_conn, empty_queue):
    org, registry = await setup(su_conn, app_engine, locality="cloud", allow_cloud=False)
    _, inv, calls, _ = await run_one(app_engine, org, registry)
    assert inv["verdict"]["ai"]["status"] == "refused" and calls == [] and FakeProvider.seen == []


async def test_budget_and_capacity_limits(app_engine, su_conn, empty_queue):
    org, registry = await setup(su_conn, app_engine, budget=0)
    _, inv, calls, _ = await run_one(app_engine, org, registry)
    assert inv["verdict"]["ai"]["status"] == "budget_exhausted" and calls == []
    # all slots held by someone else: no wait configured -> unavailable, nothing sent
    org, registry = await setup(su_conn, app_engine)
    await su_conn.execute(
        "UPDATE aegis.llm_slots SET holder = 'other', lease_expires_at = now() + interval '1 hour' WHERE slot = 1"
    )
    try:
        _, inv, calls, _ = await run_one(app_engine, org, registry)
    finally:
        await su_conn.execute("UPDATE aegis.llm_slots SET holder = NULL, lease_expires_at = NULL")
    assert (
        inv["verdict"]["ai"]["status"] == "unavailable"
        and "capacity" in inv["verdict"]["ai"]["reason"]
    )
    assert FakeProvider.seen == []


async def test_slot_pool_bounds_concurrency(app_engine):
    from aegis.llm import slots

    a = await slots.acquire(app_engine, "h1", 2, 60)
    b = await slots.acquire(app_engine, "h2", 2, 60)
    c = await slots.acquire(app_engine, "h3", 2, 60)
    try:
        assert {a, b} == {1, 2} and c is None
        assert await slots.renew(app_engine, a, "h1", 60) and not await slots.renew(
            app_engine, a, "intruder", 60
        )
    finally:
        for s, h in ((a, "h1"), (b, "h2")):
            await slots.release(app_engine, s, h)
    assert await slots.acquire(app_engine, "h4", 1, 60) == 1
    await slots.release(app_engine, 1, "h4")
