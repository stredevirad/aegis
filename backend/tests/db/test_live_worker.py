"""Worker with the live source, driven by recorded backend responses (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert, select, text

from aegis.collection.live import LiveCollector
from aegis.config import Profile, Settings
from aegis.db.engine import tenant_tx
from aegis.db.tables import integrations, investigations, snapshots
from aegis.integrations.egress import EgressError, EgressResponse, check_path
from aegis.integrations.registry import Connector, ConnectorKind, Registry
from aegis.investigations.sources import ScenarioRegistry
from aegis.jobs import queue
from aegis.jobs.queue import Windows
from aegis.security.crypto import Keyring
from aegis.worker.runner import Worker

pytestmark = pytest.mark.db

FIX = Path(__file__).resolve().parents[1] / "integrations" / "fixtures"
# the recorded fixtures cover 2026-10-02 00:30:58 → 00:40:58 UTC; the catalog fault began 00:33:45
WIN = Windows(
    incident_start=datetime(2026, 10, 2, 0, 33, 15, tzinfo=UTC),
    incident_end=datetime(2026, 10, 2, 0, 40, 50, tzinfo=UTC),
    baseline_start=datetime(2026, 10, 2, 0, 31, 0, tzinfo=UTC),
    baseline_end=datetime(2026, 10, 2, 0, 33, 15, tzinfo=UTC),
)


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class RecordedEgress:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    async def request(self, connector, method, path, *, params=None, json_body=None, **_):
        check_path(connector.kind, path)
        self.calls += 1
        if self.fail:
            raise EgressError("connect", f"cannot connect to {connector.id}")
        q = (params or {}).get("query", "")
        if connector.kind is ConnectorKind.PROMETHEUS:
            body = _load(
                "prometheus_error_ratio.json"
                if "5.." in q
                else "prometheus_latency_p95.json"
                if "histogram" in q
                else "prometheus_request_rate.json"
            )
        elif connector.kind is ConnectorKind.JAEGER:
            body = _load("jaeger_traces_frontend.json")
        else:
            body = _load("loki_logs.json")
        return EgressResponse(200, {}, json.dumps(body).encode(), f"{method} {connector.id} {path}")

    async def aclose(self):
        pass


async def _org_with_integrations(su_conn, app_engine, tenant_slug_prefix="live"):
    import secrets

    slug = f"{tenant_slug_prefix}-{secrets.token_hex(3)}"
    org = await su_conn.fetchval(
        "INSERT INTO aegis.organizations (slug, name) VALUES ($1, $1) RETURNING id", slug
    )
    conns = [
        Connector(id="p1", kind=ConnectorKind.PROMETHEUS, url="http://prom.test:9090", orgs=[slug]),
        Connector(id="j1", kind=ConnectorKind.JAEGER, url="http://jaeger.test:16686", orgs=[slug]),
        Connector(id="l1", kind=ConnectorKind.LOKI, url="http://loki.test:3100", orgs=[slug]),
    ]
    async with tenant_tx(app_engine, org) as c:
        for cn in conns:
            await c.execute(
                insert(integrations).values(
                    org_id=org, connector_id=cn.id, kind=cn.kind.value, name=cn.id
                )
            )
    return org, Registry(conns)


async def _enqueue_live(app_engine, org, attempts=3):
    async with tenant_tx(app_engine, org) as c:
        inc = (
            await c.execute(
                text(
                    "INSERT INTO incidents (org_id, title, severity, service, started_at) VALUES (:o, 'x', 'high', 'frontend', :t) RETURNING id"
                ),
                {"o": org, "t": WIN.incident_start},
            )
        ).scalar_one()
        res = await queue.enqueue_investigation(
            c,
            org_id=org,
            incident_id=inc,
            windows=WIN,
            source={"kind": "live"},
            trigger="manual",
            requested_by="t",
            idempotency_key=None,
            queue_limit=100,
            max_attempts=attempts,
        )
    return res.investigation_id


def _settings(**kw):
    return Settings(profile=Profile.TEST, job_heartbeat_seconds=1, **kw)


async def test_live_investigation_from_recorded_backends(app_engine, su_conn, empty_queue):
    org, registry = await _org_with_integrations(su_conn, app_engine)
    settings = _settings()
    collector = LiveCollector(
        app_engine, registry, Keyring.from_settings(settings), RecordedEgress()
    )
    inv_id = await _enqueue_live(app_engine, org)
    w = Worker(
        app_engine, settings, ScenarioRegistry(None), worker_id="w-live", collector=collector
    )
    assert await w.process(await queue.claim(app_engine, w.worker_id, 30, 4)) == "succeeded"
    async with tenant_tx(app_engine, org) as c:
        inv = (
            (await c.execute(select(investigations).where(investigations.c.id == inv_id)))
            .mappings()
            .one()
        )
        snap = (
            (await c.execute(select(snapshots).where(snapshots.c.investigation_id == inv_id)))
            .mappings()
            .one()
        )
    assert inv["status"] == "completed" and inv["classification"] in (
        "PLAUSIBLE",
        "CONFIRMED",
        "INSUFFICIENT",
    )
    assert snap["synthetic"] is False and snap["redaction"]["applied"] is True
    statuses = {o["modality"]: o["status"] for o in snap["payload"]["collection"]}
    assert statuses == {
        "metrics": "ok",
        "traces": "partial",
        "logs": "partial",
        "changes": "not_configured",
    } or (statuses["metrics"] == "ok" and statuses["changes"] == "not_configured")
    # the change feed was never configured for this organization: change-induced is not claimable
    assert all(
        rc["mechanism"] != "change_induced:feature_flag" for rc in inv["verdict"]["root_causes"]
    )
    assert any("change" in g.lower() for g in inv["verdict"]["coverage_gaps"])


async def test_all_backends_down_retries_then_reports_coverage_gaps(
    app_engine, su_conn, empty_queue
):
    org, registry = await _org_with_integrations(su_conn, app_engine)
    settings = _settings(job_max_attempts=2)
    egress = RecordedEgress(fail=True)
    collector = LiveCollector(app_engine, registry, Keyring.from_settings(settings), egress)
    inv_id = await _enqueue_live(app_engine, org, attempts=2)
    w = Worker(
        app_engine, settings, ScenarioRegistry(None), worker_id="w-down", collector=collector
    )
    assert await w.process(await queue.claim(app_engine, w.worker_id, 30, 4)) == "retry_scheduled"
    async with tenant_tx(app_engine, org) as c:
        await c.execute(
            text("UPDATE investigation_jobs SET available_at = now() WHERE investigation_id = :i"),
            {"i": inv_id},
        )
    job = await queue.claim(app_engine, w.worker_id, 30, 4)
    assert job.attempt == 2
    assert await w.process(job) == "succeeded"  # the last attempt still produces an honest result
    async with tenant_tx(app_engine, org) as c:
        inv = (
            (await c.execute(select(investigations).where(investigations.c.id == inv_id)))
            .mappings()
            .one()
        )
    assert inv["classification"] in ("INSUFFICIENT", "NO_ANOMALY")
    assert inv["classification"] != "NO_ANOMALY" or inv["verdict"]["coverage_gaps"]
    gaps = " ".join(inv["verdict"]["coverage_gaps"]).lower()
    assert "metrics collection failed" in gaps and "traces collection failed" in gaps


async def test_org_without_integrations_fails_permanently_with_reason(
    app_engine, su_conn, empty_queue
):
    import secrets

    org = await su_conn.fetchval(
        "INSERT INTO aegis.organizations (slug, name) VALUES ($1, $1) RETURNING id",
        f"none-{secrets.token_hex(3)}",
    )
    settings = _settings()
    collector = LiveCollector(
        app_engine, Registry([]), Keyring.from_settings(settings), RecordedEgress()
    )
    inv_id = await _enqueue_live(app_engine, org)
    w = Worker(
        app_engine, settings, ScenarioRegistry(None), worker_id="w-none", collector=collector
    )
    assert await w.process(await queue.claim(app_engine, w.worker_id, 30, 4)) == "failed"
    async with tenant_tx(app_engine, org) as c:
        inv = (
            (await c.execute(select(investigations).where(investigations.c.id == inv_id)))
            .mappings()
            .one()
        )
    assert "no usable telemetry integration" in inv["error"]
