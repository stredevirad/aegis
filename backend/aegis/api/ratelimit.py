"""In-process token-bucket rate limiting.

Limits are per API replica: with N replicas the effective limit is up to N times higher.
That is acceptable for the stated scale (a few replicas) and avoids a shared rate-limit
store; the per-organization queue limits in PostgreSQL are the global backstop against
queue flooding (security model C20).
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, max_keys: int = 10_000) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._max_keys = max_keys

    def check(self, key: str, per_minute: int, burst: int) -> float | None:
        """Consume one token. Returns None if allowed, else seconds until a token is available."""
        now = time.monotonic()
        rate = per_minute / 60.0
        b = self._buckets.get(key)
        if b is None:
            if len(self._buckets) >= self._max_keys:
                self._evict(now)
            b = _Bucket(tokens=float(burst), updated=now)
            self._buckets[key] = b
        b.tokens = min(float(burst), b.tokens + (now - b.updated) * rate)
        b.updated = now
        if b.tokens >= 1.0:
            b.tokens -= 1.0
            return None
        return (1.0 - b.tokens) / rate if rate > 0 else 60.0

    def _evict(self, now: float) -> None:
        # drop the least recently used half
        ordered = sorted(self._buckets.items(), key=lambda kv: kv[1].updated)
        for k, _ in ordered[: len(ordered) // 2]:
            del self._buckets[k]
