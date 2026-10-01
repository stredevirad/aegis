"""Small, dependency-free robust statistics used by the deterministic analysis."""

from __future__ import annotations

import math
from collections.abc import Sequence

MAD_TO_SIGMA = 1.4826


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median of empty sequence")
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile, q in [0, 1]."""
    if not values:
        raise ValueError("quantile of empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be within [0, 1]")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def mad_sigma(values: Sequence[float]) -> float:
    """Median absolute deviation scaled to a normal-distribution sigma."""
    m = median(values)
    return MAD_TO_SIGMA * median([abs(v - m) for v in values])


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (used by the benchmark reports)."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))
