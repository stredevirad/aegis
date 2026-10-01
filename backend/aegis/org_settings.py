"""Per-organization settings stored in ``organizations.settings`` (validated on read and write).

Unknown keys are rejected on write and ignored on read (forward compatibility). Every value
has a safe default: automatic investigation is on only when a default source is available,
cloud LLM use is off, remediation execution is off.
"""

from __future__ import annotations

from typing import Annotated, Any

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


def read_settings(raw: dict[str, Any] | None) -> OrgSettings:
    data = {k: v for k, v in (raw or {}).items() if k in OrgSettings.model_fields}
    try:
        return OrgSettings.model_validate(data)
    except ValidationError:
        return OrgSettings()
