"""Tests for Phase 3.6 Evidence Replay & Reproducibility module."""
import os
import tempfile
from datetime import datetime, timezone

import pytest

from src.domain import Evidence, EvidenceType, Incident, InvestigationStatus
from src.persistence.replay import (
    EvidenceSnapshot,
    ReplayProvider,
    create_snapshot_from_incident,
    load_snapshot,
    reconstruct_evidence,
    save_snapshot,
)


@pytest.fixture
def sample_incident() -> Incident:
    now = datetime.now(timezone.utc)
    evidence = [
        Evidence(
            id="ev_metric_1",
            type=EvidenceType.METRIC,
            source_provider="prometheus",
            service_resource="frontend",
            summary="HTTP 500 error count: 42",
            data={"metric_name": "http_errors", "value": 42},
            observed_at=now,
            collected_at=now,
        ),
        Evidence(
            id="ev_log_1",
            type=EvidenceType.LOG,
            source_provider="opensearch",
            service_resource="product-catalog",
            summary="[ERROR] database connection pool exhausted",
            data={"level": "ERROR", "message": "connection pool exhausted"},
            observed_at=now,
            collected_at=now,
        ),
        Evidence(
            id="ev_trace_1",
            type=EvidenceType.TRACE,
            source_provider="jaeger",
            service_resource="frontend",
            summary="Trace: GET /api/products | Duration: 550ms",
            data={"trace_id": "trace_abc_123", "spans": []},
            observed_at=now,
            collected_at=now,
        ),
    ]
    return Incident(
        id="inc_test_123",
        title="Test Incident",
        description="Testing replay serialization",
        start_time=now,
        status=InvestigationStatus.RECORDED,
        scenario_id="scenario_test_001",
        affected_service="frontend",
        evidence=evidence,
    )


def test_create_and_serialize_snapshot(sample_incident: Incident) -> None:
    snap = create_snapshot_from_incident(sample_incident, metadata={"note": "unit test"})
    assert snap.snapshot_id == "snap_inc_test_123"
    assert snap.scenario_id == "scenario_test_001"
    assert len(snap.evidence_items) == 3
    assert snap.metadata["note"] == "unit test"

    json_str = snap.to_json()
    assert "ev_metric_1" in json_str
    assert "ev_log_1" in json_str
    assert "ev_trace_1" in json_str

    deserialized = EvidenceSnapshot.from_json(json_str)
    assert deserialized.snapshot_id == snap.snapshot_id
    assert len(deserialized.evidence_items) == 3


def test_save_and_load_snapshot(sample_incident: Incident) -> None:
    snap = create_snapshot_from_incident(sample_incident)
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, "snapshot.json")
        save_snapshot(snap, file_path)
        assert os.path.exists(file_path)

        loaded = load_snapshot(file_path)
        assert loaded.snapshot_id == snap.snapshot_id
        assert len(loaded.evidence_items) == 3

        reconstructed = reconstruct_evidence(loaded)
        assert len(reconstructed) == 3
        assert reconstructed[0].id == "ev_metric_1"
        assert reconstructed[0].type == EvidenceType.METRIC
        assert reconstructed[1].id == "ev_log_1"
        assert reconstructed[1].type == EvidenceType.LOG
        assert reconstructed[2].id == "ev_trace_1"
        assert reconstructed[2].type == EvidenceType.TRACE


def test_replay_provider_queries(sample_incident: Incident) -> None:
    snap = create_snapshot_from_incident(sample_incident)
    provider = ReplayProvider(snap)

    status = provider.get_status()
    assert status.health.value == "HEALTHY"

    now = datetime.now(timezone.utc)
    metrics = provider.query_metrics("frontend", now, now)
    assert len(metrics) == 1
    assert metrics[0].id == "ev_metric_1"

    logs = provider.search_logs("product-catalog", now, now)
    assert len(logs) == 1
    assert logs[0].id == "ev_log_1"

    traces = provider.get_related_traces("frontend", now, now)
    assert len(traces) == 1
    assert traces[0].id == "ev_trace_1"

    trace_by_id = provider.get_trace("trace_abc_123")
    assert trace_by_id is not None
    assert trace_by_id.id == "ev_trace_1"
