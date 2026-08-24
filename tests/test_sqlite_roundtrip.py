import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from src.domain import (
    DiagnosisClassification,
    Evidence,
    EvidenceType,
    Hypothesis,
    Incident,
    InvestigationStatus,
    RemediationRecommendation,
)
from src.persistence.repository import SQLiteRepository


@pytest.fixture
def repo():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    repository = SQLiteRepository(db_path=db_path)
    yield repository
    try:
        os.remove(db_path)
    except OSError:
        pass

def test_sqlite_roundtrip_healthy(repo):
    start_time = datetime.now(timezone.utc)
    incident = Incident(
        id="test_healthy_1",
        title="Test Healthy",
        description="Testing normal baseline",
        start_time=start_time,
        affected_service="frontend",
        status=InvestigationStatus.RECORDED,
        diagnosis="No significant anomaly detected",
        diagnosis_classification=DiagnosisClassification.NO_ANOMALY,
        end_time=start_time + timedelta(minutes=1)
    )
    
    # Add some evidence
    ev = Evidence(
        id="ev_metric_1",
        type=EvidenceType.METRIC,
        source_provider="prometheus",
        service_resource="frontend",
        summary="Metric is fine",
        data={"value": 10},
        observed_at=start_time,
        collected_at=start_time
    )
    incident.evidence.append(ev)
    
    # Save
    repo.save_incident(incident)
    
    # Load
    loaded = repo.load_incident("test_healthy_1")
    assert loaded is not None
    assert loaded.id == "test_healthy_1"
    assert loaded.status == InvestigationStatus.RECORDED
    assert loaded.diagnosis_classification == DiagnosisClassification.NO_ANOMALY
    assert loaded.diagnosis == "No significant anomaly detected"
    assert len(loaded.evidence) == 1
    assert loaded.evidence[0].id == "ev_metric_1"

def test_sqlite_roundtrip_fault(repo):
    start_time = datetime.now(timezone.utc)
    incident = Incident(
        id="test_fault_1",
        title="Test Fault",
        description="Testing fault scenario",
        start_time=start_time,
        affected_service="recommendation",
        status=InvestigationStatus.RECORDED,
        diagnosis="Cache failure detected in recommendation service",
        diagnosis_classification=DiagnosisClassification.CONFIRMED,
        scenario_id="scenario_recommendation_cache_failure",
        end_time=start_time + timedelta(minutes=2)
    )
    
    hyp = Hypothesis(
        id="hyp_1",
        description="Recommendation service has a cache failure",
        confidence=0.9,
        supporting_evidence_ids=["ev_log_1"]
    )
    incident.hypotheses.append(hyp)
    
    rec = RemediationRecommendation(
        action="Turn off flagd feature flag",
        reason="Cache failure",
        expected_impact="Fix recommendations",
        risk_uncertainty="None",
        supporting_evidence_ids=["ev_log_1"]
    )
    
    # Save
    repo.save_incident(incident)
    repo.save_recommendation(incident.id, rec)
    
    # Load
    loaded = repo.load_incident("test_fault_1")
    assert loaded.scenario_id == "scenario_recommendation_cache_failure"
    assert loaded.diagnosis_classification == DiagnosisClassification.CONFIRMED
    assert len(loaded.hypotheses) == 1
    assert loaded.hypotheses[0].confidence == 0.9
    assert loaded.hypotheses[0].supporting_evidence_ids == ["ev_log_1"]
    recs = repo.load_recommendations("test_fault_1")
    assert len(recs) == 1
    assert recs[0].action == "Turn off flagd feature flag"

def test_sqlite_roundtrip_failed(repo):
    start_time = datetime.now(timezone.utc)
    incident = Incident(
        id="test_fail_1",
        title="Test Fail",
        description="Testing LLM failure",
        start_time=start_time,
        status=InvestigationStatus.FAILED,
        end_time=start_time + timedelta(minutes=1)
    )
    
    # Save
    repo.save_incident(incident)
    
    # Load
    loaded = repo.load_incident("test_fail_1")
    assert loaded.status == InvestigationStatus.FAILED
