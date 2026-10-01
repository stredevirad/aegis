"""The pure RCA core (ADR-008): no I/O, no clock, no randomness."""

from aegis.core.analyze import analyze
from aegis.core.model import AnalysisRequest, TelemetrySnapshot
from aegis.core.result import Analysis

__all__ = ["Analysis", "AnalysisRequest", "TelemetrySnapshot", "analyze"]
