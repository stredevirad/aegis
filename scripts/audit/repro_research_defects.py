"""Reproductions for defects cited in docs/production-readiness-audit.md.

Runs fully offline against the research engine (src/) using mock providers and a
temporary SQLite file. Each check prints CONFIRMED or NOT REPRODUCED. The script
does not modify the research code; it only exercises it.

Usage (from repo root):
    python scripts/audit/repro_research_defects.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.adapters.mock_providers import (  # noqa: E402
    MockLogsProvider,
    MockMetricsProvider,
    MockTopologyProvider,
    MockTracesProvider,
)
from src.domain import (  # noqa: E402
    Evidence,
    EvidenceType,
    Incident,
    InvestigationStatus,
    LogsProvider,
    ProviderStatus,
    ToolCall,
)
from src.domain.models import HealthStatus  # noqa: E402
from src.investigation.engine import InvestigationEngine  # noqa: E402
from src.investigation.tools import ToolDispatcher  # noqa: E402
from src.persistence.events import InvestigationEvent  # noqa: E402
from src.persistence.repository import SQLiteRepository  # noqa: E402
from tests.mock_llm import MockLLMProvider  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, confirmed: bool, detail: str) -> None:
    RESULTS.append((name, confirmed, detail))


def _incident(incident_id: str, start: datetime | None = None) -> Incident:
    return Incident(
        id=incident_id,
        title="t",
        description="d",
        start_time=start or datetime.now(timezone.utc),
        status=InvestigationStatus.INCIDENT_CREATED,
        affected_service="payment-api",
    )


def _evidence(ev_id: str) -> Evidence:
    now = datetime.now(timezone.utc)
    return Evidence(
        id=ev_id,
        type=EvidenceType.TRACE,
        source_provider="jaeger",
        service_resource="frontend",
        summary="Trace: GET /",
        observed_at=now,
        collected_at=now,
    )


def check_evidence_reassigned_between_incidents(db: str) -> None:
    """Jaeger evidence IDs are ev_trace_{trace_id}: the same trace seen by two
    incidents maps to one row, and INSERT OR REPLACE moves it to the later one."""
    repo = SQLiteRepository(db_path=db)
    a, b = _incident("inc_A"), _incident("inc_B")
    a.evidence.append(_evidence("ev_trace_abc123"))
    b.evidence.append(_evidence("ev_trace_abc123"))
    repo.save_incident(a)
    repo.save_incident(b)
    loaded_a = repo.load_incident("inc_A")
    assert loaded_a is not None
    lost = len(loaded_a.evidence) == 0
    record(
        "Evidence row silently reassigned to another incident",
        lost,
        f"inc_A evidence after inc_B saved the same trace: {len(loaded_a.evidence)} items",
    )


def check_events_overwritten(db: str) -> None:
    """Event IDs are evt_{epoch_ms}; two events in the same millisecond collide and
    INSERT OR REPLACE drops the first one from the audit trail."""
    repo = SQLiteRepository(db_path=db)
    ts = datetime.now(timezone.utc)
    for action in ("STATE_TRANSITION", "TOOL_EXECUTION"):
        repo.save_event(
            InvestigationEvent(
                id="evt_1700000000000",
                incident_id="inc_evt",
                timestamp=ts,
                state=InvestigationStatus.COLLECTING_EVIDENCE,
                action=action,
                summary=action,
            )
        )
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT count(*) FROM events WHERE incident_id='inc_evt'").fetchone()[0]
    record(
        "Audit events overwritten on same-millisecond ID collision",
        n == 1,
        f"2 events written with the ID scheme evt_{{ms}}, {n} retained",
    )

    # How often does it happen in a real (mock) run? Count events emitted vs stored.
    db2 = str(Path(db).with_suffix(".run.db"))
    repo2 = SQLiteRepository(db_path=db2)
    emitted: list[str] = []
    original = repo2.save_event

    def counting_save(event: InvestigationEvent) -> None:
        emitted.append(event.id)
        original(event)

    repo2.save_event = counting_save  # type: ignore[method-assign]
    start = datetime.now(timezone.utc)
    disp = ToolDispatcher(
        MockMetricsProvider(),
        MockLogsProvider(),
        MockTopologyProvider(),
        incident_start_time=start,
        traces_provider=MockTracesProvider(),
    )
    eng = InvestigationEngine(_incident("inc_run", start), MockLLMProvider(), disp, repo2)
    eng.run()
    with sqlite3.connect(db2) as conn:
        stored = conn.execute("SELECT count(*) FROM events WHERE incident_id='inc_run'").fetchone()[0]
    record(
        "Audit events lost during a normal mock investigation",
        stored < len(emitted),
        f"emitted {len(emitted)} events, {len(set(emitted))} distinct IDs, {stored} stored",
    )


def check_misclassification() -> None:
    """_classify_diagnosis falls back to substring matching: 'healthy' anywhere in
    the diagnosis (including 'unhealthy' / 'not healthy') yields NO_ANOMALY."""
    eng = object.__new__(InvestigationEngine)
    diagnosis = "payment-api is unhealthy: connection pool exhausted causing 500s"
    cls = InvestigationEngine._classify_diagnosis(eng, diagnosis, f"DIAGNOSIS: {diagnosis}")
    record(
        "Active fault classified NO_ANOMALY by keyword fallback",
        cls.value == "NO_ANOMALY",
        f"'{diagnosis}' -> {cls.value}",
    )


def check_fabricated_citations() -> None:
    """When the model cites no evidence ID, the engine attaches evidence chosen by
    keyword heuristics, so the stored recommendation looks evidence-backed."""
    eng = object.__new__(InvestigationEngine)
    inc = _incident("inc_cite")
    inc.evidence = [_evidence("ev_trace_unrelated_1"), _evidence("ev_trace_unrelated_2")]
    eng.incident = inc
    cited = InvestigationEngine._extract_supporting_evidence_ids(
        eng, "RECOMMENDATION: restart the database | REASON: hunch"
    )
    record(
        "Uncited diagnosis receives fabricated supporting evidence IDs",
        len(cited) > 0,
        f"model cited nothing; engine attached {cited}",
    )


class _WindowSpyLogs(LogsProvider):
    def __init__(self) -> None:
        self.windows: list[tuple[datetime, datetime]] = []

    def get_status(self) -> ProviderStatus:
        return ProviderStatus(provider="spy", health=HealthStatus.HEALTHY)

    def search_logs(self, query: str, start_time: datetime, end_time: datetime) -> list[Evidence]:
        self.windows.append((start_time, end_time))
        return []


def check_query_window_ignores_incident_time() -> None:
    """Tools query the last 15 minutes relative to now(), not the incident window,
    so investigating an incident that started 2 hours ago queries the wrong data."""
    started = datetime.now(timezone.utc) - timedelta(hours=2)
    spy = _WindowSpyLogs()
    disp = ToolDispatcher(
        MockMetricsProvider(), spy, MockTopologyProvider(), incident_start_time=started
    )
    disp.dispatch(ToolCall(id="x", name="search_logs", arguments={"query": "payment-api"}))
    win_start, win_end = spy.windows[0]
    misses = win_start > started + timedelta(minutes=30)
    record(
        "Telemetry window anchored to now() instead of incident start",
        misses,
        f"incident start {started:%H:%M}Z, queried {win_start:%H:%M}-{win_end:%H:%M}Z",
    )


def check_trace_status_discarded() -> None:
    """The Jaeger adapter drops span tags, so otel.status_code=ERROR never reaches the
    engine; error-trace filtering falls back to keywords in the summary."""
    from src.adapters.jaeger import JaegerProvider

    trace = {
        "traceID": "t1",
        "processes": {"p1": {"serviceName": "product-catalog"}},
        "spans": [
            {
                "spanID": "s1",
                "operationName": "GetProduct",
                "processID": "p1",
                "startTime": 1_700_000_000_000_000,
                "duration": 5000,
                "references": [],
                "tags": [
                    {"key": "otel.status_code", "value": "ERROR"},
                    {"key": "rpc.grpc.status_code", "value": 13},
                ],
            }
        ],
    }
    ev = JaegerProvider("http://unused")._normalize_trace(trace)
    assert ev is not None
    blob = (ev.summary + str(ev.data)).lower()
    record(
        "Span error status discarded by Jaeger normalization",
        "error" not in blob and "status" not in blob,
        f"normalized summary/data contain no status: {ev.summary!r}",
    )


def check_connections_not_closed(db: str) -> None:
    """`with sqlite3.connect(...)` commits but does not close; every repository call
    leaks a connection. On Windows the database file stays locked afterwards."""
    repo = SQLiteRepository(db_path=db)
    repo.save_incident(_incident("inc_lock"))
    try:
        os.rename(db, db + ".moved")
        os.rename(db + ".moved", db)
        locked = False
    except PermissionError:
        locked = True
    record(
        "SQLite connections leaked (file still locked after repository call)",
        locked if os.name == "nt" else False,
        "rename after save_incident raised PermissionError"
        if locked
        else "file not locked (non-Windows hosts do not lock; inspect manually)",
    )


def main() -> int:
    # ignore_cleanup_errors: the research repository leaks connections (see last check),
    # which keeps the files locked on Windows.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        check_evidence_reassigned_between_incidents(str(Path(tmp) / "a.db"))
        check_events_overwritten(str(Path(tmp) / "b.db"))
        check_misclassification()
        check_fabricated_citations()
        check_query_window_ignores_incident_time()
        check_trace_status_discarded()
        check_connections_not_closed(str(Path(tmp) / "c.db"))
    width = max(len(n) for n, _, _ in RESULTS)
    for name, confirmed, detail in RESULTS:
        flag = "CONFIRMED     " if confirmed else "NOT REPRODUCED"
        print(f"[{flag}] {name.ljust(width)} | {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
