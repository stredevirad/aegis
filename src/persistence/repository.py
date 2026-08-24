import json
import sqlite3
from datetime import datetime, timezone

from src.domain import (
    DiagnosisClassification,
    Hypothesis,
    HypothesisStatus,
    Incident,
    InvestigationStatus,
    RemediationRecommendation,
)
from src.persistence.events import InvestigationEvent


class SQLiteRepository:
    def __init__(self, db_path: str = "aegis.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    description TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    status TEXT,
                    environment_profile TEXT,
                    scenario_id TEXT,
                    affected_service TEXT,
                    diagnosis TEXT,
                    diagnosis_classification TEXT
                )
            """
            )
            # Lightweight schema migration for existing databases
            for col in ("affected_service", "diagnosis", "diagnosis_classification"):
                try:
                    conn.execute(f"ALTER TABLE incidents ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
            
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hypotheses (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT,
                    description TEXT,
                    status TEXT,
                    confidence REAL,
                    supporting_evidence_ids TEXT,
                    contradicting_evidence_ids TEXT
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT,
                    type TEXT,
                    source_provider TEXT,
                    service_resource TEXT,
                    summary TEXT,
                    data TEXT,
                    observed_at TEXT,
                    collected_at TEXT
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT,
                    timestamp TEXT,
                    state TEXT,
                    action TEXT,
                    summary TEXT,
                    evidence_ids TEXT,
                    tool_name TEXT,
                    duration_ms REAL
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recommendations (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT,
                    action TEXT,
                    reason TEXT,
                    expected_impact TEXT,
                    risk_uncertainty TEXT,
                    supporting_evidence_ids TEXT
                )
            """
            )
            conn.commit()

    def save_incident(self, incident: Incident) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO incidents 
                (id, title, description, start_time, end_time, status, environment_profile, scenario_id, affected_service, diagnosis, diagnosis_classification) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident.id,
                    incident.title,
                    incident.description,
                    incident.start_time.isoformat(),
                    incident.end_time.isoformat() if incident.end_time else None,
                    incident.status.value,
                    incident.environment_profile,
                    incident.scenario_id,
                    incident.affected_service,
                    incident.diagnosis,
                    incident.diagnosis_classification.value if incident.diagnosis_classification else None,
                ),
            )

            for h in incident.hypotheses:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO hypotheses 
                    (id, incident_id, description, status, confidence, supporting_evidence_ids, contradicting_evidence_ids) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        h.id,
                        incident.id,
                        h.description,
                        h.status.value,
                        h.confidence,
                        json.dumps(h.supporting_evidence_ids),
                        json.dumps(h.contradicting_evidence_ids),
                    ),
                )

            for e in incident.evidence:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO evidence 
                    (id, incident_id, type, source_provider, service_resource, summary, data, observed_at, collected_at) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        e.id,
                        incident.id,
                        e.type.value,
                        e.source_provider,
                        e.service_resource,
                        e.summary,
                        json.dumps(e.data),
                        e.observed_at.isoformat(),
                        e.collected_at.isoformat(),
                    ),
                )
            conn.commit()

    def save_event(self, event: InvestigationEvent) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO events 
                (id, incident_id, timestamp, state, action, summary, evidence_ids, tool_name, duration_ms) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.incident_id,
                    event.timestamp.isoformat(),
                    event.state.value,
                    event.action,
                    event.summary,
                    json.dumps(event.evidence_ids),
                    event.tool_name,
                    event.duration_ms,
                ),
            )
            conn.commit()

    def save_recommendation(self, incident_id: str, rec: RemediationRecommendation) -> None:
        rec_id = f"rec_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO recommendations 
                (id, incident_id, action, reason, expected_impact, risk_uncertainty, supporting_evidence_ids) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec_id,
                    incident_id,
                    rec.action,
                    rec.reason,
                    rec.expected_impact,
                    rec.risk_uncertainty,
                    json.dumps(rec.supporting_evidence_ids),
                ),
            )
            conn.commit()

    def load_incident(self, incident_id: str) -> Incident | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            row = cursor.fetchone()
            if not row:
                return None

            incident = Incident(
                id=row["id"],
                title=row["title"],
                description=row["description"],
                start_time=datetime.fromisoformat(row["start_time"]),
                status=InvestigationStatus(row["status"]),
                environment_profile=row["environment_profile"],
                scenario_id=row["scenario_id"],
                affected_service=row["affected_service"],
                diagnosis=row["diagnosis"],
                diagnosis_classification=DiagnosisClassification(row["diagnosis_classification"]) if row["diagnosis_classification"] else None,
            )
            if row["end_time"]:
                incident.end_time = datetime.fromisoformat(row["end_time"])

            # Load hypotheses
            cursor = conn.execute("SELECT * FROM hypotheses WHERE incident_id = ?", (incident_id,))
            for h_row in cursor.fetchall():
                incident.hypotheses.append(Hypothesis(
                    id=h_row["id"],
                    description=h_row["description"],
                    status=HypothesisStatus(h_row["status"]),
                    confidence=h_row["confidence"],
                    supporting_evidence_ids=json.loads(h_row["supporting_evidence_ids"]),
                    contradicting_evidence_ids=json.loads(h_row["contradicting_evidence_ids"])
                ))
                
            # Load evidence
            cursor = conn.execute("SELECT * FROM evidence WHERE incident_id = ?", (incident_id,))
            from src.domain.models import Evidence, EvidenceType
            for e_row in cursor.fetchall():
                incident.evidence.append(Evidence(
                    id=e_row["id"],
                    type=EvidenceType(e_row["type"]),
                    source_provider=e_row["source_provider"],
                    service_resource=e_row["service_resource"],
                    summary=e_row["summary"],
                    data=json.loads(e_row["data"]),
                    observed_at=datetime.fromisoformat(e_row["observed_at"]),
                    collected_at=datetime.fromisoformat(e_row["collected_at"])
                ))
                
            return incident

    def load_recommendations(self, incident_id: str) -> list[RemediationRecommendation]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM recommendations WHERE incident_id = ?", (incident_id,))
            recs = []
            for row in cursor.fetchall():
                recs.append(RemediationRecommendation(
                    action=row["action"],
                    reason=row["reason"],
                    expected_impact=row["expected_impact"],
                    risk_uncertainty=row["risk_uncertainty"],
                    supporting_evidence_ids=json.loads(row["supporting_evidence_ids"])
                ))
            return recs
