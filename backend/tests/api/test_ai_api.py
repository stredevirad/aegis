"""AI settings validation and the AI/egress records exposed by the API."""

from __future__ import annotations

import json

import httpx
import pytest

from aegis.api.app import create_app
from aegis.config import Profile, Settings
from aegis.llm.adjudicate import Adjudicator
from aegis.security.crypto import Keyring
from aegis.worker.runner import Worker
from tests.api.conftest import BASE, CSRF, SCENARIOS
from tests.db.test_ai_adjudication import FakeProvider, good_output
from tests.llm.test_validate import analysis

pytestmark = pytest.mark.db


@pytest.fixture
async def ai_world(tenant_factory, member_factory, app_engine, registry, tmp_path):
    a, b = await tenant_factory("aia"), await tenant_factory("aib")
    f = tmp_path / "c.yaml"
    f.write_text(
        "connectors:\n"
        f"  - {{id: llm-local, kind: llm, url: 'http://ollama.test:11434', orgs: [{a.slug}], options: {{api: ollama, locality: local, default_model: qwen-test}}}}\n"
        f"  - {{id: llm-cloud, kind: llm, url: 'https://api.anthropic.com', orgs: [{a.slug}], options: {{api: anthropic, locality: cloud, default_model: claude-opus-5-5}}}}\n"
        f"  - {{id: prom, kind: prometheus, url: 'http://prom.test:9090', orgs: [{a.slug}]}}\n",
        encoding="utf-8",
    )
    settings = Settings(
        profile=Profile.TEST,
        auth_mode="dev",
        public_base_url=BASE,
        allowed_origins=[BASE],
        scenario_dir=SCENARIOS,
        connectors_file=f,
        job_heartbeat_seconds=1,
        llm_slot_wait_seconds=0,
    )
    app = create_app(settings, engine=app_engine, scenarios=registry)
    clients = []

    async def login(t, role="admin"):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
        clients.append(c)
        await c.post(
            "/auth/dev/login",
            json={"email": await member_factory(t, role), "org": t.slug},
            headers=CSRF,
        )
        return c

    yield {"a": a, "b": b, "login": login, "app": app, "settings": settings}
    for c in clients:
        await c.aclose()


async def test_ai_settings_require_an_llm_integration_and_cloud_opt_in(ai_world):
    admin = await ai_world["login"](ai_world["a"])
    url = "/api/v1/organization/settings"
    assert (await admin.patch(url, json={"ai_enabled": True}, headers=CSRF)).json()["error"][
        "code"
    ] == "invalid_ai_provider"
    prom = (
        await admin.post(
            "/api/v1/integrations", json={"connector_id": "prom", "name": "p"}, headers=CSRF
        )
    ).json()
    r = await admin.patch(
        url, json={"ai_enabled": True, "ai_integration_id": prom["id"]}, headers=CSRF
    )
    assert r.json()["error"]["code"] == "invalid_ai_provider"
    cloud = (
        await admin.post(
            "/api/v1/integrations",
            json={
                "connector_id": "llm-cloud",
                "name": "c",
                "credential": {
                    "type": "header",
                    "header_name": "x-api-key",
                    "header_value": "sk-ant-test",
                },
            },
            headers=CSRF,
        )
    ).json()
    r = await admin.patch(
        url, json={"ai_enabled": True, "ai_integration_id": cloud["id"]}, headers=CSRF
    )
    assert r.json()["error"]["code"] == "cloud_not_allowed"
    r = await admin.patch(
        url,
        json={"ai_enabled": True, "ai_integration_id": cloud["id"], "ai_allow_cloud": True},
        headers=CSRF,
    )
    assert r.status_code == 200 and r.json()["settings"]["ai_allow_cloud"] is True
    investigator = await ai_world["login"](ai_world["a"], "investigator")
    assert (
        await investigator.patch(url, json={"ai_enabled": False}, headers=CSRF)
    ).status_code == 403


async def test_investigation_exposes_ai_assessment_and_llm_calls(ai_world, app_engine, empty_queue):
    admin = await ai_world["login"](ai_world["a"])
    local = (
        await admin.post(
            "/api/v1/integrations",
            json={"connector_id": "llm-local", "name": "ollama"},
            headers=CSRF,
        )
    ).json()
    await admin.patch(
        "/api/v1/organization/settings",
        json={
            "ai_enabled": True,
            "ai_integration_id": local["id"],
            "default_source": {"kind": "scenario", "scenario": "s05-deployment-regression"},
        },
        headers=CSRF,
    )
    inc = (
        await admin.post("/api/v1/incidents", json={"title": "x", "severity": "high"}, headers=CSRF)
    ).json()
    inv = (
        await admin.post(f"/api/v1/incidents/{inc['id']}/investigations", json={}, headers=CSRF)
    ).json()
    a = analysis("s05-deployment-regression")
    FakeProvider.script = [
        good_output(
            "catalog", a.verdict.root_causes[0].supporting_evidence_ids, "PLAUSIBLE", "moderate"
        )
    ]
    FakeProvider.seen = []
    state = ai_world["app"].state.aegis
    adj = Adjudicator(
        app_engine,
        ai_world["settings"],
        state.registry,
        Keyring.from_settings(ai_world["settings"]),
        egress=None,
        provider_factory=FakeProvider,
    )
    w = Worker(
        app_engine, ai_world["settings"], state.scenarios, worker_id="w-ai-api", adjudicator=adj
    )
    from aegis.jobs import queue

    assert await w.process(await queue.claim(app_engine, w.worker_id, 60, 4)) == "succeeded"
    got = (await admin.get(f"/api/v1/investigations/{inv['id']}")).json()
    assert got["classification"] == "CONFIRMED"  # deterministic
    ai = got["ai"]
    assert (
        ai["status"] == "accepted" and ai["classification"] == "PLAUSIBLE" and ai["lowered"] is True
    )
    assert ai["claims"][0]["evidence_ids"] and ai["provider"] == "llm-local"
    events = [
        e["kind"] for e in (await admin.get(f"/api/v1/investigations/{inv['id']}/events")).json()
    ]
    assert events.index("diagnosis") < events.index("ai_adjudication") < events.index("completed")
    calls = (await admin.get(f"/api/v1/investigations/{inv['id']}/llm-calls")).json()
    assert len(calls) == 1 and calls[0]["model"] == "qwen-test" and calls[0]["locality"] == "local"
    assert "prompt" not in json.dumps(calls).replace("prompt_sha256", "").replace(
        "prompt_bytes", ""
    ).replace("prompt_tokens", "")
    other = await ai_world["login"](ai_world["b"])
    assert (await other.get(f"/api/v1/investigations/{inv['id']}/llm-calls")).status_code == 404
