"""Process-wide state shared by request handlers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from aegis.api.ratelimit import RateLimiter
from aegis.auth.oidc import OidcClient
from aegis.config import Settings
from aegis.integrations.egress import EgressClient
from aegis.integrations.registry import Registry
from aegis.investigations.sources import ScenarioRegistry
from aegis.security.crypto import Keyring


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    scenarios: ScenarioRegistry
    registry: Registry
    keyring: Keyring
    egress: EgressClient
    limiter: RateLimiter = field(default_factory=RateLimiter)
    open_streams: Counter[str] = field(default_factory=Counter)
    key_touched: dict[str, float] = field(default_factory=dict)
    # True when the deployment can collect live telemetry at all (registry has telemetry
    # connectors); whether a given organization can is checked per request.
    live_collection_available: bool = False
    oidc: OidcClient | None = None
