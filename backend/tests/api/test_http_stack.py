"""The slice over a real network socket: uvicorn on TCP + Worker.run + an HTTP client.

The SSE assertion is discriminating: the client must receive the ``queued`` event *before*
any worker exists. A transport or proxy that buffers the response body until the stream ends
would time out here (the stream cannot end until the worker completes the investigation).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket

import httpx
import pytest
import uvicorn

from aegis.worker.runner import Worker
from tests.api.conftest import alertmanager_payload

pytestmark = pytest.mark.db


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.asynccontextmanager
async def serve(app):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, lifespan="on")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


async def test_alert_to_diagnosis_over_tcp_with_live_sse(
    app, settings, app_engine, registry, tenant_factory, member_factory, empty_queue
):
    t = await tenant_factory("tcp")
    email = await member_factory(t, "admin")
    async with serve(app) as base, httpx.AsyncClient(base_url=base, timeout=30) as admin:
        csrf = {"X-Aegis-CSRF": "1", "Origin": settings.public_base_url}
        # the configured origin is the public URL; the TCP base differs only by port
        assert (
            await admin.post("/auth/dev/login", json={"email": email, "org": t.slug}, headers=csrf)
        ).status_code == 200
        assert (
            await admin.patch(
                "/api/v1/organization/settings",
                json={
                    "default_source": {"kind": "scenario", "scenario": "s06-feature-flag-failure"}
                },
                headers=csrf,
            )
        ).status_code == 200
        key = (
            await admin.post(
                "/api/v1/api-keys", json={"name": "am", "scopes": ["ingest:alerts"]}, headers=csrf
            )
        ).json()["secret"]
        ingest = await admin.post(
            "/api/v1/alerts/alertmanager",
            json=alertmanager_payload(),
            headers={"Authorization": f"Bearer {key}"},
        )
        assert ingest.status_code == 202, ingest.text
        incident_id = ingest.json()["incidents_created"][0]
        inv_id = (await admin.get(f"/api/v1/incidents/{incident_id}")).json()[
            "latest_investigation"
        ]["id"]

        async with admin.stream("GET", f"/api/v1/investigations/{inv_id}/stream") as stream:
            lines = stream.aiter_lines()

            async def next_event() -> dict:
                async for line in lines:
                    if line.startswith("data: "):
                        return json.loads(line[6:])
                raise AssertionError("stream ended")

            first = await asyncio.wait_for(next_event(), timeout=10)  # no worker running yet
            assert first["kind"] == "queued"

            worker = Worker(app_engine, settings, registry, worker_id="w-tcp", poll_seconds=0.1)
            stop = asyncio.Event()
            runner = asyncio.create_task(worker.run(stop))
            kinds = [first["kind"]]
            try:
                while kinds[-1] not in ("completed", "failed", "cancelled"):
                    kinds.append((await asyncio.wait_for(next_event(), timeout=60))["kind"])
            finally:
                stop.set()
                await asyncio.wait_for(runner, timeout=30)
        assert kinds[-1] == "completed" and "analyzing" in kinds and "diagnosis" in kinds

        inv = (await admin.get(f"/api/v1/investigations/{inv_id}")).json()
        assert (
            inv["classification"] == "CONFIRMED" and inv["root_causes"][0]["service"] == "catalog"
        )
        assert inv["root_causes"][0]["mechanism"] == "change_induced:feature_flag"
        ready = await admin.get("/readyz")
        assert ready.status_code == 200
