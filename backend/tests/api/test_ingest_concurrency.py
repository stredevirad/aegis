"""Concurrent ingestion over overlapping groups with mixed re-deliveries (deadlock regression).

Before the per-organization ingestion lock, a payload touching groups {G1, G2} that held the
audit-chain lock while waiting for G2 deadlocked with a payload holding G2 and waiting for
the audit lock; re-deliveries also locked incident rows before any group lock.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from tests.api.conftest import bearer, make_key

pytestmark = pytest.mark.db

SERVICES = ["cart", "checkout", "payments", "catalog", "shipping"]


def _payload(rng: random.Random, n: int) -> dict:
    alerts = []
    for svc in rng.sample(SERVICES, k=rng.randint(2, 4)):
        for _ in range(rng.randint(1, 3)):
            fp = f"{svc}-{rng.randint(0, 5)}"  # small space: many re-deliveries
            alerts.append(
                {
                    "status": "firing",
                    "labels": {"alertname": "E", "service": svc, "severity": "high"},
                    "annotations": {"summary": f"payload {n}"},
                    "startsAt": "2026-10-01T20:00:00Z",
                    "fingerprint": fp,
                }
            )
    return {"version": "4", "alerts": alerts}


async def test_concurrent_overlapping_payloads_never_deadlock(
    tenant_factory, member_factory, client_factory
):
    t = await tenant_factory("conc")
    admin = await client_factory(await member_factory(t, "admin"), t.slug)
    key = await make_key(admin, "ingest:alerts")
    rng = random.Random(42)
    for round_ in range(3):
        clients = [await client_factory() for _ in range(20)]
        payloads = [_payload(rng, i) for i in range(20)]
        responses = await asyncio.gather(
            *(
                c.post("/api/v1/alerts/alertmanager", json=p, headers=bearer(key))
                for c, p in zip(clients, payloads, strict=True)
            )
        )
        codes = [r.status_code for r in responses]
        assert codes == [202] * 20, (
            round_,
            [r.text[:200] for r in responses if r.status_code != 202],
        )
    incidents = (await admin.get("/api/v1/incidents", params={"limit": 50})).json()["items"]
    # one incident per service group, however the payloads interleaved
    assert sorted(i["service"] for i in incidents) == sorted(SERVICES)
    total_alerts = sum(i["alert_count"] for i in incidents)
    assert total_alerts == len(
        {(s, n) for s in SERVICES for n in range(6)}
        & {
            (a["service"], int(a["fingerprint"].rsplit("-", 1)[1]))
            for i in incidents
            for a in (
                await admin.get(f"/api/v1/incidents/{i['id']}/alerts", params={"limit": 200})
            ).json()["items"]
        }
    )
    verify = (await admin.get("/api/v1/audit/verify")).json()
    assert verify["intact"]
