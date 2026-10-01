"""The M2 vertical slice through the real API: alert → incident → investigation → worker →
cited verdict → events/SSE → audit. PostgreSQL, as aegis_app."""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.api.conftest import CSRF, alertmanager_payload, bearer, make_key, run_worker_once

pytestmark = pytest.mark.db


async def _admin_with_scenario(
    tenant_factory, member_factory, client_factory, scenario="s05-deployment-regression"
):
    t = await tenant_factory("slice")
    admin = await client_factory(await member_factory(t, "admin"), t.slug)
    r = await admin.patch(
        "/api/v1/organization/settings",
        json={
            "default_source": {"kind": "scenario", "scenario": scenario},
            "auto_investigate": True,
        },
        headers=CSRF,
    )
    assert r.status_code == 200, r.text
    return t, admin


async def test_alert_to_cited_diagnosis(
    tenant_factory, member_factory, client_factory, app_engine, settings, registry, empty_queue
):
    _t, admin = await _admin_with_scenario(tenant_factory, member_factory, client_factory)
    key = await make_key(admin, "ingest:alerts")
    ingest = await client_factory()

    r = await ingest.post(
        "/api/v1/alerts/alertmanager", json=alertmanager_payload(), headers=bearer(key)
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert len(body["incidents_created"]) == 1 and body["new_occurrences"] == 1
    incident_id = body["incidents_created"][0]
    assert body["investigations"][incident_id] == "queued"

    detail = (await admin.get(f"/api/v1/incidents/{incident_id}")).json()
    assert detail["severity"] == "critical" and detail["service"] == "checkout"
    inv = detail["latest_investigation"]
    assert inv["status"] == "queued" and inv["synthetic"] is True and inv["trigger"] == "auto"

    assert await run_worker_once(app_engine, settings, registry) == "succeeded"

    inv = (await admin.get(f"/api/v1/investigations/{inv['id']}")).json()
    assert inv["status"] == "completed"
    assert inv["classification"] in ("CONFIRMED", "PLAUSIBLE")
    assert [rc["service"] for rc in inv["root_causes"]] == ["catalog"]
    cited = set(inv["root_causes"][0]["supporting_evidence_ids"])
    assert cited

    # every cited evidence ID resolves to a stored, provenance-carrying evidence record
    for eid in cited:
        ev = await admin.get(f"/api/v1/investigations/{inv['id']}/evidence/{eid}")
        assert ev.status_code == 200, eid
        assert ev.json()["sources"], "evidence without provenance"

    page = (
        await admin.get(f"/api/v1/investigations/{inv['id']}/evidence", params={"limit": 5})
    ).json()
    assert len(page["items"]) == 5 and page["next_cursor"]
    page2 = (
        await admin.get(
            f"/api/v1/investigations/{inv['id']}/evidence",
            params={"limit": 5, "cursor": page["next_cursor"]},
        )
    ).json()
    assert {e["evidence_id"] for e in page["items"]}.isdisjoint(
        {e["evidence_id"] for e in page2["items"]}
    )

    graph = (await admin.get(f"/api/v1/investigations/{inv['id']}/graph")).json()
    assert any(c["service"] == "catalog" and c["checks"] for c in graph["candidates"])
    assert graph["decision_trace"]

    snap = (await admin.get(f"/api/v1/investigations/{inv['id']}/snapshot")).json()
    assert snap["synthetic"] is True and snap["counts"]["spans"] > 0 and snap["collection"]

    events = (await admin.get(f"/api/v1/investigations/{inv['id']}/events")).json()
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "queued" and kinds[-1] == "completed"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    # SSE replays from Last-Event-ID and ends after the terminal event
    async with admin.stream(
        "GET", f"/api/v1/investigations/{inv['id']}/stream", headers={"Last-Event-ID": "3"}
    ) as s:
        assert s.headers["content-type"].startswith("text/event-stream")
        text = "".join([chunk async for chunk in s.aiter_text()])
    ids = [int(line[4:]) for line in text.splitlines() if line.startswith("id: ")]
    assert ids == list(range(4, len(events) + 1))
    datas = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    assert datas[-1]["kind"] == "completed"

    audit = (await admin.get("/api/v1/audit")).json()
    actions = {a["action"] for a in audit["items"]}
    assert {
        "alerts.ingested",
        "incident.created",
        "investigation.started",
        "investigation.completed",
    } <= actions
    verify = (await admin.get("/api/v1/audit/verify")).json()
    assert verify["intact"] is True and verify["events"] >= 5


async def test_live_progress_over_sse_while_worker_runs(
    tenant_factory, member_factory, client_factory, app_engine, settings, registry, empty_queue
):
    _t, admin = await _admin_with_scenario(tenant_factory, member_factory, client_factory)
    inc = (
        await admin.post(
            "/api/v1/incidents", json={"title": "manual", "severity": "high"}, headers=CSRF
        )
    ).json()
    inv = await admin.post(f"/api/v1/incidents/{inc['id']}/investigations", json={}, headers=CSRF)
    assert inv.status_code == 201, inv.text
    inv_id = inv.json()["id"]

    async def consume() -> list[str]:
        async with admin.stream("GET", f"/api/v1/investigations/{inv_id}/stream") as s:
            return [
                line[7:]
                for line in [ln async for ln in s.aiter_lines()]
                if line.startswith("event: ")
            ]

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.3)
    assert await run_worker_once(app_engine, settings, registry) == "succeeded"
    kinds = await asyncio.wait_for(consumer, timeout=30)
    assert kinds[0] == "queued" and kinds[-1] == "completed"
    assert "analyzing" in kinds and "diagnosis" in kinds


async def test_start_is_idempotent_and_one_active(
    tenant_factory, member_factory, client_factory, empty_queue
):
    _t, admin = await _admin_with_scenario(tenant_factory, member_factory, client_factory)
    inc = (
        await admin.post("/api/v1/incidents", json={"title": "x", "severity": "low"}, headers=CSRF)
    ).json()
    url = f"/api/v1/incidents/{inc['id']}/investigations"
    first = await admin.post(url, json={}, headers={**CSRF, "Idempotency-Key": "abc-1"})
    again = await admin.post(url, json={}, headers={**CSRF, "Idempotency-Key": "abc-1"})
    other = await admin.post(url, json={}, headers=CSRF)
    assert first.status_code == 201
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert other.status_code == 200 and other.json()["id"] == first.json()["id"]
    inc2 = (
        await admin.post("/api/v1/incidents", json={"title": "y", "severity": "low"}, headers=CSRF)
    ).json()
    clash = await admin.post(
        f"/api/v1/incidents/{inc2['id']}/investigations",
        json={},
        headers={**CSRF, "Idempotency-Key": "abc-1"},
    )
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "idempotency_conflict"


async def test_cancel_and_retry(
    tenant_factory, member_factory, client_factory, app_engine, settings, registry, empty_queue
):
    _t, admin = await _admin_with_scenario(tenant_factory, member_factory, client_factory)
    inc = (
        await admin.post("/api/v1/incidents", json={"title": "x", "severity": "low"}, headers=CSRF)
    ).json()
    inv = (
        await admin.post(f"/api/v1/incidents/{inc['id']}/investigations", json={}, headers=CSRF)
    ).json()
    r = await admin.post(f"/api/v1/investigations/{inv['id']}/cancel", headers=CSRF)
    assert r.status_code == 202 and r.json()["result"] == "cancelled"
    assert (
        await admin.post(f"/api/v1/investigations/{inv['id']}/cancel", headers=CSRF)
    ).status_code == 409
    retry = await admin.post(f"/api/v1/investigations/{inv['id']}/retry", headers=CSRF)
    assert retry.status_code == 201 and retry.json()["id"] != inv["id"]
    assert retry.json()["incident_window"] == inv["incident_window"]
    assert await run_worker_once(app_engine, settings, registry) == "succeeded"


async def test_unavailable_sources_are_rejected(tenant_factory, member_factory, client_factory):
    t = await tenant_factory("src")
    admin = await client_factory(await member_factory(t, "admin"), t.slug)
    inc = (
        await admin.post("/api/v1/incidents", json={"title": "x", "severity": "low"}, headers=CSRF)
    ).json()
    url = f"/api/v1/incidents/{inc['id']}/investigations"
    live = await admin.post(url, json={}, headers=CSRF)  # no default source, live not configured
    assert live.status_code == 400 and live.json()["error"]["code"] == "source_unavailable"
    bogus = await admin.post(
        url, json={"source": {"kind": "scenario", "scenario": "no-such"}}, headers=CSRF
    )
    assert bogus.status_code == 400
    traversal = await admin.post(
        url, json={"source": {"kind": "scenario", "scenario": "../../etc/passwd"}}, headers=CSRF
    )
    assert traversal.status_code == 422
    srcs = (await admin.get("/api/v1/sources")).json()
    assert srcs["live"] is False and "s05-deployment-regression" in srcs["scenarios"]


async def test_queue_full_rejects_manual_start(
    tenant_factory, member_factory, client_factory, settings, empty_queue
):
    _t, admin = await _admin_with_scenario(tenant_factory, member_factory, client_factory)
    statuses = []
    for i in range(settings.org_queue_limit + 1):
        inc = (
            await admin.post(
                "/api/v1/incidents", json={"title": f"i{i}", "severity": "low"}, headers=CSRF
            )
        ).json()
        statuses.append(
            await admin.post(f"/api/v1/incidents/{inc['id']}/investigations", json={}, headers=CSRF)
        )
    assert all(s.status_code == 201 for s in statuses[:-1])
    assert statuses[-1].status_code == 429
    assert (
        statuses[-1].json()["error"]["code"] == "queue_full"
        and "retry-after" in statuses[-1].headers
    )
