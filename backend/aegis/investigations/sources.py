"""Where an investigation's telemetry snapshot comes from.

* ``scenario`` — a synthetic benchmark scenario from the operator-configured scenario
  directory (``AEGIS_SCENARIO_DIR``). Deterministic, always marked ``synthetic`` in the
  snapshot and the API, used for demos and for exercising the pipeline without live
  backends. Scenario IDs are looked up in a registry loaded at startup; they are never used
  to build a file path.
* ``live`` — collection from the organization's registered integrations (roadmap M4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from aegis.benchmark.synth import ScenarioSpec, build_request, generate
from aegis.core.model import AnalysisRequest, TelemetrySnapshot
from aegis.jobs.queue import Windows


class _Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScenarioSource(_Source):
    kind: Literal["scenario"] = "scenario"
    scenario: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]


class LiveSource(_Source):
    kind: Literal["live"] = "live"


SourceSpec = Annotated[ScenarioSource | LiveSource, Field(discriminator="kind")]


class SourceUnavailable(Exception):
    """The requested source is not configured in this deployment."""


class ScenarioRegistry:
    def __init__(self, directory: Path | None) -> None:
        self._specs: dict[str, ScenarioSpec] = {}
        if directory is None:
            return
        for path in sorted(directory.glob("*.yaml")):
            spec = ScenarioSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            self._specs[spec.id] = spec

    def ids(self) -> list[str]:
        return sorted(self._specs)

    def get(self, scenario_id: str) -> ScenarioSpec:
        try:
            return self._specs[scenario_id]
        except KeyError:
            raise SourceUnavailable(f"scenario {scenario_id!r} is not available") from None

    def windows(self, scenario_id: str) -> Windows:
        req = build_request(self.get(scenario_id))
        base = req.baselines[0]
        return Windows(req.incident.start, req.incident.end, base.start, base.end)

    def request(self, scenario_id: str) -> AnalysisRequest:
        return build_request(self.get(scenario_id))

    def snapshot(self, scenario_id: str) -> tuple[TelemetrySnapshot, AnalysisRequest]:
        return generate(self.get(scenario_id))
