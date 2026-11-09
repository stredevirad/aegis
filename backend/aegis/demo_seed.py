"""Demo data for the one-command stack (``aegis admin seed-demo``).

Creates four synthetic incidents and queues one scenario investigation for each. They go through
the same queue the API uses, so the worker investigates them like any other incident. The
recorded benchmark (``eval/scenarios``) expects:

* s06 feature-flag failure: CONFIRMED / strong, root cause ``catalog``;
* s09 red herring: PLAUSIBLE / moderate on the true cause (``catalog``), not the noisy decoy;
* s10 prompt injection in logs: PLAUSIBLE / moderate on ``catalog``; the injected log text can't
  raise the verdict;
* s08 insufficient evidence: INSUFFICIENT; no root cause is named and a next check is given.

Every incident says in its description that it is synthetic, and scenario investigations carry
the ``synthetic`` flag the UI badges.

Idempotent: a seeded incident is found again by ``source = 'demo-seed'`` plus its scenario label,
and each enqueue carries a per-scenario idempotency key, so a re-run creates nothing new.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from aegis import audit
from aegis.audit import Action, Actor
from aegis.db.engine import system_tx, tenant_tx
from aegis.db.tables import incidents
from aegis.investigations.sources import ScenarioRegistry
from aegis.jobs import queue

SEED_SOURCE = "demo-seed"
SEED_LABEL = "demo_seed"
ACTOR = Actor.system("demo-seed")


@dataclass(frozen=True)
class DemoIncident:
    scenario: str
    title: str
    severity: str
    service: str


DEMO_INCIDENTS: tuple[DemoIncident, ...] = (
    DemoIncident(
        "s06-feature-flag-failure",
        "Checkout error rate above 5% on the storefront",
        "critical",
        "frontend",
    ),
    DemoIncident(
        "s09-red-herring",
        "Product pages failing while the nightly batch job runs slow",
        "high",
        "frontend",
    ),
    DemoIncident(
        "s10-prompt-injection-in-logs",
        "Frontend 5xx spike with suspicious error log messages",
        "high",
        "frontend",
    ),
    DemoIncident(
        "s08-insufficient-evidence",
        "Frontend errors rising (edge metrics only)",
        "medium",
        "frontend",
    ),
)


@dataclass(frozen=True)
class SeedOutcome:
    scenario: str
    incident_id: UUID
    investigation_id: UUID | None
    incident_created: bool
    investigation_created: bool


class SeedError(Exception):
    """The demo data could not be created (unknown organization or scenario)."""


async def _org_id(engine: AsyncEngine, slug: str) -> UUID:
    async with system_tx(engine) as c:
        org_id = (
            await c.execute(text("SELECT aegis.org_id_for_slug(:slug)"), {"slug": slug})
        ).scalar_one_or_none()
    if org_id is None:
        raise SeedError(f"no organization {slug!r}")
    return UUID(str(org_id))


async def seed_demo(
    engine: AsyncEngine,
    scenarios: ScenarioRegistry,
    slug: str,
    *,
    queue_limit: int,
    max_attempts: int,
) -> list[SeedOutcome]:
    org_id = await _org_id(engine, slug)
    available = set(scenarios.ids())
    missing = [d.scenario for d in DEMO_INCIDENTS if d.scenario not in available]
    if missing:
        raise SeedError(f"scenarios not available (check AEGIS_SCENARIO_DIR): {', '.join(missing)}")
    outcomes: list[SeedOutcome] = []
    for demo in DEMO_INCIDENTS:
        windows = scenarios.windows(demo.scenario)
        async with tenant_tx(engine, org_id) as c:
            incident_id = (
                await c.execute(
                    select(incidents.c.id).where(
                        incidents.c.org_id == org_id,
                        incidents.c.source == SEED_SOURCE,
                        incidents.c.labels[SEED_LABEL].astext == demo.scenario,
                    )
                )
            ).scalar_one_or_none()
            incident_created = incident_id is None
            if incident_id is None:
                incident_id = (
                    await c.execute(
                        incidents.insert()
                        .values(
                            org_id=org_id,
                            title=demo.title,
                            description=(
                                "Synthetic demo incident (seeded by the aegis admin seed-demo "
                                "command). Its investigation analyses the synthetic scenario "
                                f"{demo.scenario} from eval/scenarios, not telemetry from a real "
                                "system."
                            ),
                            severity=demo.severity,
                            service=demo.service,
                            source=SEED_SOURCE,
                            labels={SEED_LABEL: demo.scenario},
                            started_at=min(windows.incident_start, datetime.now(UTC)),
                            created_by="demo-seed",
                        )
                        .returning(incidents.c.id)
                    )
                ).scalar_one()
                await audit.record(
                    c,
                    org_id,
                    ACTOR,
                    Action.INCIDENT_CREATED,
                    target_type="incident",
                    target_id=incident_id,
                    details={"source": SEED_SOURCE, "severity": demo.severity},
                )
            source = {"kind": "scenario", "scenario": demo.scenario}
            res = await queue.enqueue_investigation(
                c,
                org_id=org_id,
                incident_id=incident_id,
                windows=windows,
                source=source,
                trigger="manual",
                requested_by="demo-seed",
                idempotency_key=f"demo-seed:{demo.scenario}",
                queue_limit=queue_limit,
                max_attempts=max_attempts,
            )
            if res.created:
                await audit.record(
                    c,
                    org_id,
                    ACTOR,
                    Action.INVESTIGATION_STARTED,
                    target_type="investigation",
                    target_id=res.investigation_id,
                    details={"incident_id": str(incident_id), "source": source},
                )
        outcomes.append(
            SeedOutcome(
                demo.scenario, incident_id, res.investigation_id, incident_created, res.created
            )
        )
    return outcomes
