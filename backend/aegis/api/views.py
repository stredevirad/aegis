"""Row → response-schema conversion (one place, explicit fields)."""

from __future__ import annotations

from typing import Any

from aegis.api import schemas as S


def incident(row: Any) -> S.IncidentOut:
    return S.IncidentOut(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        severity=row["severity"],
        status=row["status"],
        service=row["service"],
        environment=row["environment"],
        source=row["source"],
        labels=row["labels"] or {},
        annotations=row["annotations"] or {},
        started_at=row["started_at"],
        resolved_at=row["resolved_at"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_alert_at=row["last_alert_at"],
        alert_count=row["alert_count"],
        grouping_open=row["group_key"] is not None,
        auto_investigated_at=row["auto_investigated_at"],
    )


def alert(row: Any) -> S.AlertOut:
    return S.AlertOut(**{f: row[f] for f in S.AlertOut.model_fields})


def _root_causes(verdict: dict[str, Any]) -> list[S.RootCauseOut]:
    return [
        S.RootCauseOut(
            service=rc["service"],
            mechanism=rc["mechanism"],
            support=rc["support"],
            supporting_evidence_ids=list(rc.get("supporting_evidence_ids", [])),
            contradicting_evidence_ids=list(rc.get("contradicting_evidence_ids", [])),
        )
        for rc in verdict.get("root_causes", [])
    ]


def investigation(row: Any) -> S.InvestigationOut:
    verdict: dict[str, Any] = row["verdict"] or {}
    source: dict[str, Any] = row["source"] or {}
    return S.InvestigationOut(
        id=row["id"],
        incident_id=row["incident_id"],
        status=row["status"],
        queue_state=row.get("job_status"),
        attempts=row.get("job_attempts"),
        trigger=row["trigger"],
        requested_by=row["requested_by"],
        source=source,
        synthetic=source.get("kind") == "scenario",
        incident_window=S.WindowOut(start=row["window_start"], end=row["window_end"]),
        baseline_window=S.WindowOut(start=row["baseline_start"], end=row["baseline_end"]),
        classification=row["classification"],
        support=row["support"],
        display_label=row["display_label"],
        summary=row["summary"],
        root_causes=_root_causes(verdict),
        reasons=list(verdict.get("reasons", [])),
        next_checks=[
            S.NextCheckOut(
                action=n["action"], reason=n["reason"], services=list(n.get("services", []))
            )
            for n in verdict.get("next_checks", [])
        ],
        coverage_gaps=list(verdict.get("coverage_gaps", [])),
        blast_radius=list(verdict.get("blast_radius", [])),
        other_anomalies=list(verdict.get("other_anomalies", [])),
        error=row["error"],
        cancel_requested=row["cancel_requested"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def event(row: Any) -> S.EventOut:
    return S.EventOut(
        seq=row["seq"],
        ts=row["ts"],
        kind=row["kind"],
        state=row["state"],
        actor=row["actor"],
        message=row["message"],
        data=row["data"] or {},
    )


def evidence(row: Any) -> S.EvidenceOut:
    return S.EvidenceOut(
        evidence_id=row["evidence_id"],
        kind=row["kind"],
        modality=row["modality"],
        service=row["service"],
        observed_at=row["observed_at"],
        summary=row["summary"],
        attributes=row["attributes"] or {},
        sources=list(row["sources"] or []),
    )


def candidate(row: Any) -> S.CandidateOut:
    return S.CandidateOut(
        service=row["service"],
        rank=row["rank"],
        rule=row["rule"],
        rule_sentence=row["rule_sentence"],
        hypothesis=row["hypothesis"],
        inferred=row["inferred"],
        features=row["features"] or {},
        checks=[S.CheckOut(**c) for c in (row["checks"] or [])],
        supporting_evidence_ids=list(row["supporting"] or []),
        contradicting_evidence_ids=list(row["contradicting"] or []),
    )


def change_event(row: Any) -> S.ChangeEventOut:
    return S.ChangeEventOut(**{f: row[f] for f in S.ChangeEventOut.model_fields})


def api_key(row: Any, now: Any) -> S.ApiKeyOut:
    return S.ApiKeyOut(
        **{f: row[f] for f in S.ApiKeyOut.model_fields if f != "active"},
        active=row["revoked_at"] is None and row["expires_at"] > now,
    )


def audit_event(row: Any) -> S.AuditEventOut:
    return S.AuditEventOut(
        seq=row["seq"],
        ts=row["ts"],
        actor_type=row["actor_type"],
        actor_id=row["actor_id"],
        action=row["action"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        details=row["details"] or {},
        request_id=row["request_id"],
        hash=bytes(row["hash"]).hex(),
    )
