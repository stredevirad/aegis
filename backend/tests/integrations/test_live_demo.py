"""Live collection against the demo stack (``docker compose -f deploy/compose.yaml --profile demo
up -d``). Marked ``live``; skipped when the stack is not reachable. Takes about 6 minutes:

1. the catalog fault flag is cleared and a 3-minute healthy baseline accumulates;
2. the flag is set to a 40 % error rate and the change is recorded in the organization's
   change feed (as the flag service reports it in the full stack);
3. after 2 minutes Aegis collects metrics (Prometheus), traces (Jaeger) and logs (Loki)
   through the egress client and the integration registry, and runs the RCA core.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import insert

from aegis.collection.live import LiveCollector
from aegis.config import Profile, Settings
from aegis.core import analyze
from aegis.core.model import CollectionStatus, Modality
from aegis.db.engine import tenant_tx
from aegis.db.tables import integrations
from aegis.ingest import changes
from aegis.integrations import registry as reg
from aegis.integrations.egress import EgressClient
from aegis.jobs.queue import Windows
from aegis.security.crypto import Keyring

pytestmark = [pytest.mark.db, pytest.mark.live]

PROM, JAEGER, LOKI, FLAGS = (
    "http://127.0.0.1:19090",
    "http://127.0.0.1:16686",
    "http://127.0.0.1:13100",
    "http://127.0.0.1:18081",
)


def _down() -> str | None:
    """Name of the first unreachable demo endpoint after up to 30 s of retries, else None."""
    import time

    probes = (f"{PROM}/-/ready", f"{JAEGER}/api/services", f"{LOKI}/ready", f"{FLAGS}/healthz")
    deadline = time.monotonic() + 30
    while True:
        failing = None
        for u in probes:
            try:
                if httpx.get(u, timeout=3).status_code >= 500:
                    failing = u
            except httpx.HTTPError:
                failing = u
            if failing:
                break
        if failing is None or time.monotonic() > deadline:
            return failing
        time.sleep(2)


async def test_live_collection_finds_the_injected_flag_fault(tenant_factory, app_engine, tmp_path):
    down = _down()
    if down:
        pytest.skip(f"demo stack not reachable ({down})")
    t = await tenant_factory("live")
    f = tmp_path / "connectors.yaml"
    f.write_text(
        "connectors:\n"
        f"  - {{id: demo-prom, kind: prometheus, url: '{PROM}', orgs: [{t.slug}], allow_loopback: true}}\n"
        f"  - {{id: demo-jaeger, kind: jaeger, url: '{JAEGER}', orgs: [{t.slug}], allow_loopback: true}}\n"
        f"  - {{id: demo-loki, kind: loki, url: '{LOKI}', orgs: [{t.slug}], allow_loopback: true}}\n",
        encoding="utf-8",
    )
    settings = Settings(profile=Profile.DEVELOPMENT, connectors_file=f)
    registry = reg.load(settings)
    async with tenant_tx(app_engine, t.org_id) as c:
        for cid, kind in (
            ("demo-prom", "prometheus"),
            ("demo-jaeger", "jaeger"),
            ("demo-loki", "loki"),
        ):
            await c.execute(
                insert(integrations).values(org_id=t.org_id, connector_id=cid, kind=kind, name=cid)
            )

    async with httpx.AsyncClient() as http:
        await http.put(
            f"{FLAGS}/flags/catalog.error_rate", json={"value": "0", "actor": "live-test"}
        )
        await asyncio.sleep(180)
        fault_at = datetime.now(UTC)
        r = await http.put(
            f"{FLAGS}/flags/catalog.error_rate", json={"value": "0.4", "actor": "live-test"}
        )
        version = r.json()["version"]
    async with tenant_tx(app_engine, t.org_id) as c:
        await changes.store(
            c,
            t.org_id,
            changes.ChangeEventIn(
                source="demo-flags",
                kind="feature_flag",
                service="catalog",
                ts=fault_at,
                actor="live-test",
                summary="flag catalog.error_rate changed from '0' to '0.4'",
                external_id=f"catalog.error_rate@{version}",
                scope={"flag_key": "catalog.error_rate", "new_value": "0.4", "old_value": "0"},
            ),
            None,
        )
    await asyncio.sleep(120)
    now = datetime.now(UTC)
    windows = Windows(
        incident_start=fault_at - timedelta(seconds=30),
        incident_end=now,
        baseline_start=fault_at - timedelta(minutes=3),
        baseline_end=fault_at - timedelta(seconds=30),
    )
    egress = EgressClient(settings)
    try:
        collector = LiveCollector(app_engine, registry, Keyring.from_settings(settings), egress)
        snapshot, request, redaction = await collector.collect(t.org_id, windows, ["frontend"])
    finally:
        await egress.aclose()
        async with httpx.AsyncClient() as http:
            await http.put(
                f"{FLAGS}/flags/catalog.error_rate", json={"value": "0", "actor": "live-test"}
            )

    outcomes = {o.modality: o for o in snapshot.collection}
    assert outcomes[Modality.METRICS].status is CollectionStatus.OK, outcomes[Modality.METRICS]
    assert outcomes[Modality.TRACES].status in (CollectionStatus.OK, CollectionStatus.PARTIAL)
    assert outcomes[Modality.LOGS].status in (CollectionStatus.OK, CollectionStatus.PARTIAL)
    assert (
        outcomes[Modality.CHANGES].status is CollectionStatus.OK
        and outcomes[Modality.CHANGES].items >= 1
    )
    assert snapshot.metrics and snapshot.spans and snapshot.logs and not snapshot.synthetic
    assert redaction["applied"] is True

    analysis = analyze(snapshot, request)
    v = analysis.verdict
    print(  # noqa: T201 - surfaced in the live-test log for the ledger
        "verdict",
        v.classification,
        v.support,
        [(rc.service, rc.mechanism.value) for rc in v.root_causes],
        v.reasons,
    )
    assert v.classification.value in ("CONFIRMED", "PLAUSIBLE"), (v.classification, v.reasons)
    assert [rc.service for rc in v.root_causes] == ["catalog"], v.root_causes
    assert all(e.sources for e in analysis.evidence)
