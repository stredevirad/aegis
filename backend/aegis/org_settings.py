"""Per-organization settings stored in ``organizations.settings`` (validated on read and write).

Unknown keys are rejected on write and ignored on read (forward compatibility). Every value
has a safe default: automatic investigation is on only when a default source is available,
cloud LLM use is off, remediation execution is off.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis.investigations.sources import SourceSpec


class OrgSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    auto_investigate: bool = True
    # Source used for automatic investigations and when a start request names none.
    default_source: SourceSpec | None = None
    grouping_window_minutes: Annotated[int, Field(ge=1, le=1440)] = 30
    auto_resolve: bool = True
    # Remediation execution is disabled until an admin enables it (ADR-014).
    remediation_enabled: bool = False
    # Two-person rule (default): the approver must differ from the proposer.
    remediation_allow_self_approval: bool = False
    # AI adjudication (ADR-009): off by default. The provider is one of the organization's
    # integrations with an ``llm`` connector; cloud connectors also need ai_allow_cloud.
    # There is never a fallback to another provider.
    ai_enabled: bool = False
    ai_integration_id: UUID | None = None
    ai_model: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    ai_allow_cloud: bool = False
    ai_daily_token_budget: Annotated[int, Field(ge=0, le=100_000_000)] = 200_000
    ai_max_output_tokens: Annotated[int, Field(ge=64, le=8192)] = 1200


def read_settings(raw: dict[str, Any] | None) -> OrgSettings:
    data = {k: v for k, v in (raw or {}).items() if k in OrgSettings.model_fields}
    try:
        return OrgSettings.model_validate(data)
    except ValidationError:
        return OrgSettings()
