"""Process-wide state shared by request handlers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from aegis.api.ratelimit import RateLimiter
from aegis.auth.oidc import OidcClient
from aegis.config import Settings
from aegis.investigations.sources import ScenarioRegistry


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    scenarios: ScenarioRegistry
    limiter: RateLimiter = field(default_factory=RateLimiter)
    open_streams: Counter[str] = field(default_factory=Counter)
    key_touched: dict[str, float] = field(default_factory=dict)
    # Live collection needs the integration registry (roadmap M4); until then only replay
    # (scenario) sources can be investigated.
    live_collection_available: bool = False
    oidc: OidcClient | None = None
