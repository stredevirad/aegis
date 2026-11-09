"""Remediation end to end against a real HTTP flag service (the demo flag app on a local port,
reached through the egress client): catalog → proposal → two-person approval → compare-and-set
execution → rollback, and the attacks ADR-014/Council C name: replay, tampering, stale or
expired approvals, self-approval, parameters from the request, execution while disabled,
cross-tenant access."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import socket
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn

from aegis.api.app import create_app
from aegis.config import Profile, Settings
from tests.api.conftest import BASE, CSRF, SCENARIOS, run_worker_once

pytestmark = pytest.mark.db
FLAGS_APP = Path(__file__).resolve().parents[3] / "demo" / "app" / "flags.py"


def _load_flags_app():
    spec = importlib.util.spec_from_file_location(f"demo_flags_{uuid.uuid4().hex[:6]}", FLAGS_APP)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.app


@contextlib.asynccontextmanager
async def flag_service():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(_load_flags_app(), host="127.0.0.1", port=port, log_config=None)
    )
    task = asyncio.create_task(server.serve())
    for _ in range(200):  # up to 10 s for uvicorn to bind
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


@pytest.fixture
async def world(tenant_factory, member_factory, app_engine, registry, tmp_path):
    a, b = await tenant_factory("ra"), await tenant_factory("rb")
    async with flag_service() as flags_url:
        f = tmp_path / "c.yaml"
        f.write_text(
            f"connectors:\n  - {{id: flags, kind: flag_service, url: '{flags_url}', orgs: [{a.slug}], allow_loopback: true}}\n",
            encoding="utf-8",
        )
        settings = Settings(
            profile=Profile.TEST,
            auth_mode="dev",
            public_base_url=BASE,
            allowed_origins=[BASE],
            scenario_dir=SCENARIOS,
            connectors_file=f,
            remediation_verify_delay_seconds=0,
        )
        app = create_app(settings, engine=app_engine, scenarios=registry)
        clients: list[httpx.AsyncClient] = []

        async def login(t, role):
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
            clients.append(c)
            r = await c.post(
                "/auth/dev/login",
                json={"email": await member_factory(t, role), "org": t.slug},
                headers=CSRF,
            )
            assert r.status_code == 200
            return c

        flags = httpx.AsyncClient(base_url=flags_url)
        admin = await login(a, "admin")
        integ = (
            await admin.post(
                "/api/v1/integrations",
                json={"connector_id": "flags", "name": "flags"},
                headers=CSRF,
            )
        ).json()
        action = (
            await admin.post(
                "/api/v1/remediation/actions",
                json={
                    "name": "Disable catalog fault",
                    "integration_id": integ["id"],
                    "service": "catalog",
                    "flag_key": "catalog.error_rate",
                    "target_value": "0",
                    "risk": "low",
                    "expected_impact": "catalog stops returning injected errors",
                },
                headers=CSRF,
            )
        ).json()
        await flags.put("/flags/catalog.error_rate", json={"value": "0.3", "actor": "chaos"})
        inc = (
            await admin.post(
                "/api/v1/incidents",
                json={"title": "catalog errors", "severity": "high", "service": "catalog"},
                headers=CSRF,
            )
        ).json()
        yield {
            "a": a,
            "b": b,
            "login": login,
            "admin": admin,
            "action": action,
            "incident": inc,
            "flags": flags,
            "settings": settings,
            "app": app,
        }
        await flags.aclose()
        for c in clients:
            await c.aclose()


async def _flag(w) -> dict:
    return (await w["flags"].get("/flags/catalog.error_rate")).json()


async def _propose(w, who):
    return await who.post(
        f"/api/v1/incidents/{w['incident']['id']}/remediations",
        json={"action_id": w["action"]["id"], "reason": "catalog error rate is the injected fault"},
        headers=CSRF,
    )


async def _enable(w):
    r = await w["admin"].patch(
        "/api/v1/organization/settings", json={"remediation_enabled": True}, headers=CSRF
    )
    assert r.status_code == 200


async def test_full_lifecycle_with_two_people(world):
    w = world
    investigator, reviewer = (
        await w["login"](w["a"], "investigator"),
        await w["login"](w["a"], "reviewer"),
    )
    p = await _propose(w, investigator)
    assert p.status_code == 201, p.text
    p = p.json()
    assert p["status"] == "proposed" and p["diff"] == {
        "flag_key": "catalog.error_rate",
        "current_value": "0.3",
        "new_value": "0",
        "rollback_value": "0.3",
        "prior_version": 1,
    }
    assert (
        await investigator.post(f"/api/v1/remediations/{p['id']}/approve", headers=CSRF)
    ).status_code == 403
    approved = (await reviewer.post(f"/api/v1/remediations/{p['id']}/approve", headers=CSRF)).json()
    assert approved["status"] == "approved" and approved["approval_expires_at"]
    # execution is disabled until an admin enables it
    r = await investigator.post(f"/api/v1/remediations/{p['id']}/execute", headers=CSRF)
    assert r.status_code == 403 and r.json()["error"]["code"] == "remediation_disabled"
    assert (
        await reviewer.post(f"/api/v1/remediations/{p['id']}/execute", headers=CSRF)
    ).status_code == 403  # no permission
    await _enable(w)
    done = (await investigator.post(f"/api/v1/remediations/{p['id']}/execute", headers=CSRF)).json()
    assert done["status"] == "verifying" and done["execution"]["ok"] is True
    assert (await _flag(w))["value"] == "0"
    replay = await investigator.post(f"/api/v1/remediations/{p['id']}/execute", headers=CSRF)
    assert replay.status_code == 409 and replay.json()["error"]["code"] == "not_executable"
    back = (
        await investigator.post(f"/api/v1/remediations/{p['id']}/rollback", headers=CSRF)
    ).json()
    assert back["status"] == "rolled_back" and (await _flag(w))["value"] == "0.3"
    assert (
        await investigator.post(f"/api/v1/remediations/{p['id']}/rollback", headers=CSRF)
    ).status_code == 409
    audit = (
        await w["admin"].get(
            "/api/v1/audit", params={"target_type": "remediation", "target_id": p["id"]}
        )
    ).json()["items"]
    actions = [x["action"] for x in audit]
    assert {
        "remediation.proposed",
        "remediation.approved",
        "remediation.executed",
        "remediation.rolled_back",
    } <= set(actions)
    approval = next(x for x in audit if x["action"] == "remediation.approved")
    assert (
        approval["details"]["diff"]["new_value"] == "0"
        and approval["details"]["inverse_approved"] is True
    )


async def test_admin_cannot_approve_own_proposal(world):
    p = (await _propose(world, world["admin"])).json()
    r = await world["admin"].post(f"/api/v1/remediations/{p['id']}/approve", headers=CSRF)
    assert r.status_code == 403 and r.json()["error"]["code"] == "two_person_rule"


async def test_stale_proposal_is_not_approvable(world):
    w = world
    p = (await _propose(w, w["admin"])).json()
    await w["flags"].put(
        "/flags/catalog.error_rate", json={"value": "0.5", "actor": "someone-else"}
    )
    reviewer = await w["login"](w["a"], "reviewer")
    r = await reviewer.post(f"/api/v1/remediations/{p['id']}/approve", headers=CSRF)
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_proposal"
    assert (await w["admin"].get(f"/api/v1/remediations/{p['id']}")).json()["status"] == "expired"


async def test_target_changed_after_approval_aborts_without_writing(world):
    w = world
    p = (await _propose(w, w["admin"])).json()
    await (await w["login"](w["a"], "reviewer")).post(
        f"/api/v1/remediations/{p['id']}/approve", headers=CSRF
    )
    await w["flags"].put(
        "/flags/catalog.error_rate", json={"value": "0.9", "actor": "someone-else"}
    )
    await _enable(w)
    r = (await w["admin"].post(f"/api/v1/remediations/{p['id']}/execute", headers=CSRF)).json()
    assert r["status"] == "failed" and "changed since approval" in r["execution"]["reason"]
    assert (await _flag(w))["value"] == "0.9"  # not overwritten


async def test_expired_or_tampered_approvals_cannot_execute(world, su_conn):
    w = world
    await _enable(w)
    reviewer = await w["login"](w["a"], "reviewer")
    p = (await _propose(w, w["admin"])).json()
    await reviewer.post(f"/api/v1/remediations/{p['id']}/approve", headers=CSRF)
    await su_conn.execute(
        "UPDATE aegis.remediation_proposals SET approval_expires_at = now() - interval '1 second' WHERE id = $1",
        uuid.UUID(p["id"]),
    )
    r = await w["admin"].post(f"/api/v1/remediations/{p['id']}/execute", headers=CSRF)
    assert r.status_code == 409 and r.json()["error"]["code"] == "approval_expired"
    # a fresh proposal, approved, then its parameters altered in the database
    p2 = (await _propose(w, w["admin"])).json()
    await reviewer.post(f"/api/v1/remediations/{p2['id']}/approve", headers=CSRF)
    await su_conn.execute(
        """UPDATE aegis.remediation_proposals SET params = jsonb_set(params, '{value}', '"999"') WHERE id = $1""",
        uuid.UUID(p2["id"]),
    )
    r = await w["admin"].post(f"/api/v1/remediations/{p2['id']}/execute", headers=CSRF)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_executable"
    assert (await _flag(w))["value"] == "0.3"


async def test_request_cannot_supply_parameters_and_viewers_cannot_propose(world):
    w = world
    r = await w["admin"].post(
        f"/api/v1/incidents/{w['incident']['id']}/remediations",
        json={
            "action_id": w["action"]["id"],
            "reason": "x",
            "flag_key": "payments.kill",
            "value": "1",
        },
        headers=CSRF,
    )
    assert r.status_code == 422
    viewer = await w["login"](w["a"], "viewer")
    assert (await _propose(w, viewer)).status_code == 403
    r = await w["admin"].post(
        "/api/v1/remediation/actions",
        json={
            "name": "bad",
            "integration_id": str(uuid.uuid4()),
            "flag_key": "x",
            "target_value": "1",
            "risk": "low",
        },
        headers=CSRF,
    )
    assert r.status_code == 400


async def test_cross_tenant_proposals_are_invisible(world):
    w = world
    p = (await _propose(w, w["admin"])).json()
    other = await w["login"](w["b"], "admin")
    for method, path in [
        ("GET", f"/api/v1/remediations/{p['id']}"),
        ("POST", f"/api/v1/remediations/{p['id']}/approve"),
        ("POST", f"/api/v1/remediations/{p['id']}/execute"),
        ("POST", f"/api/v1/remediations/{p['id']}/rollback"),
    ]:
        assert (await other.request(method, path, headers=CSRF)).status_code == 404, path
    assert (await other.get("/api/v1/remediation/actions")).json() == []


async def test_suggestions_follow_the_deterministic_root_cause(world, app_engine, empty_queue):
    w = world
    await w["admin"].patch(
        "/api/v1/organization/settings",
        json={"default_source": {"kind": "scenario", "scenario": "s06-feature-flag-failure"}},
        headers=CSRF,
    )
    inv = (
        await w["admin"].post(
            f"/api/v1/incidents/{w['incident']['id']}/investigations", json={}, headers=CSRF
        )
    ).json()
    assert (
        await run_worker_once(app_engine, w["settings"], w["app"].state.aegis.scenarios)
        == "succeeded"
    )
    sugg = (
        await w["admin"].get(f"/api/v1/incidents/{w['incident']['id']}/remediation-suggestions")
    ).json()
    assert [s["action"]["flag_key"] for s in sugg] == ["catalog.error_rate"]
    assert (
        "root cause" in sugg[0]["why"]
        and sugg[0]["investigation_id"] == inv["id"]
        and sugg[0]["evidence_ids"]
    )


async def test_verification_of_a_synthetic_diagnosis_is_not_evaluable(
    world, app_engine, empty_queue
):
    from aegis.remediation.runner import Verifier
    from aegis.remediation.service import Remediation

    w = world
    await _enable(w)
    await w["admin"].patch(
        "/api/v1/organization/settings",
        json={"default_source": {"kind": "scenario", "scenario": "s06-feature-flag-failure"}},
        headers=CSRF,
    )
    inv = (
        await w["admin"].post(
            f"/api/v1/incidents/{w['incident']['id']}/investigations", json={}, headers=CSRF
        )
    ).json()
    await run_worker_once(app_engine, w["settings"], w["app"].state.aegis.scenarios)
    p = (
        await w["admin"].post(
            f"/api/v1/incidents/{w['incident']['id']}/remediations",
            json={"action_id": w["action"]["id"], "investigation_id": inv["id"], "reason": "x"},
            headers=CSRF,
        )
    ).json()
    await (await w["login"](w["a"], "reviewer")).post(
        f"/api/v1/remediations/{p['id']}/approve", headers=CSRF
    )
    await w["admin"].post(f"/api/v1/remediations/{p['id']}/execute", headers=CSRF)
    st = w["app"].state.aegis
    verifier = Verifier(
        app_engine,
        Remediation(app_engine, st.settings, st.registry, st.keyring, st.egress),
        collector=None,
    )
    assert await verifier.run_due("w-verify") >= 1
    got = (await w["admin"].get(f"/api/v1/remediations/{p['id']}")).json()
    assert (
        got["status"] == "verification_failed" and got["verification"]["result"] == "not_evaluable"
    )
    assert (
        "synthetic" in got["verification"]["reason"]
        and got["verification"]["target_unchanged_since_execution"] is True
    )


async def test_stranded_execution_is_reconciled_and_unblocks_new_proposals(
    world, app_engine, su_conn
):
    """A crash between committing 'executing' and the final update must not strand the row
    (it would block every new proposal for the action and incident forever)."""
    from aegis.remediation.runner import reconcile_stuck
    from aegis.remediation.service import Remediation

    w = world
    p = (await _propose(w, w["admin"])).json()
    await su_conn.execute(
        "UPDATE aegis.remediation_proposals SET status = 'executing', updated_at = now() - interval '10 minutes' WHERE id = $1",
        uuid.UUID(p["id"]),
    )
    assert (await _propose(w, w["admin"])).status_code == 409  # blocked while stranded
    st = w["app"].state.aegis
    n = await reconcile_stuck(
        app_engine,
        Remediation(app_engine, st.settings, st.registry, st.keyring, st.egress),
        "w-sweep",
    )
    assert n == 1
    got = (await w["admin"].get(f"/api/v1/remediations/{p['id']}")).json()
    assert got["status"] == "failed" and got["execution"]["uncertain"] is True
    assert got["execution"]["observed"] == {
        "value": "0.3",
        "version": 1,
    }  # re-read, not re-executed
    assert (await _flag(w))["value"] == "0.3"
    assert (await _propose(w, w["admin"])).status_code == 201
    # a fresh 'executing' row (a request still in flight) is left alone
    assert (
        await reconcile_stuck(
            app_engine,
            Remediation(app_engine, st.settings, st.registry, st.keyring, st.egress),
            "w-sweep",
        )
        == 0
    )
