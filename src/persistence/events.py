from pydantic import AwareDatetime, BaseModel, Field

from src.domain import InvestigationStatus


class InvestigationEvent(BaseModel):
    """A single event in the investigation timeline."""

    id: str
    incident_id: str
    timestamp: AwareDatetime
    state: InvestigationStatus
    action: str
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    tool_name: str | None = None
    duration_ms: float | None = None
