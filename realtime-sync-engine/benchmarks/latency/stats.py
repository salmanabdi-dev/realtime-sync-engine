"""Latency statistics (nearest-rank percentiles, no numpy needed)."""

from __future__ import annotations

import math


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return float("nan")
    rank = max(1, math.ceil(p / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


def summarize_ns(samples_ns: list[int]) -> dict:
    values = sorted(s / 1e6 for s in samples_ns)
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "mean_ms": sum(values) / len(values),
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99),
        "max_ms": values[-1],
        "min_ms": values[0],
    }


def histogram(samples_ns: list[int], edges_ms=(1, 2, 5, 10, 20, 40, 80, 160)) -> dict:
    buckets = {f"<={e}ms": 0 for e in edges_ms}
    buckets[f">{edges_ms[-1]}ms"] = 0
    for s in samples_ns:
        ms = s / 1e6
        for e in edges_ms:
            if ms <= e:
                buckets[f"<={e}ms"] += 1
                break
        else:
            buckets[f">{edges_ms[-1]}ms"] += 1
    return buckets
