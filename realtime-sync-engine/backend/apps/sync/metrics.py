"""Per-process stage timings so latency can be attributed (parse, DB commit,
Redis publish, ...). Exposed at /api/metrics/sync/ for the benchmark."""

from __future__ import annotations

import math
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager

from django.conf import settings


def percentile(sorted_values: list[float], p: float) -> float:
    """Nearest-rank percentile on pre-sorted data."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(p / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


class StageMetrics:
    def __init__(self, maxlen: int = 50_000):
        self._maxlen = maxlen
        self._lock = threading.Lock()
        self._samples: dict[str, deque] = defaultdict(lambda: deque(maxlen=self._maxlen))
        self.counters: dict[str, int] = defaultdict(int)

    @staticmethod
    def enabled() -> bool:
        return settings.SYNC.get("METRICS_ENABLED", False)

    def record(self, stage: str, millis: float) -> None:
        if self.enabled():
            self._samples[stage].append(millis)

    def incr(self, name: str, amount: int = 1) -> None:
        if self.enabled():
            with self._lock:
                self.counters[name] += amount

    def summary(self) -> dict:
        stages = {}
        for stage, samples in list(self._samples.items()):
            values = sorted(samples)
            if not values:
                continue
            stages[stage] = {
                "count": len(values),
                "mean_ms": round(sum(values) / len(values), 3),
                "p50_ms": round(percentile(values, 50), 3),
                "p95_ms": round(percentile(values, 95), 3),
                "p99_ms": round(percentile(values, 99), 3),
                "max_ms": round(values[-1], 3),
            }
        return {"pid": os.getpid(), "stages": stages, "counters": dict(self.counters)}

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self.counters.clear()


metrics = StageMetrics()


@contextmanager
def timed(stage: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        metrics.record(stage, (time.perf_counter() - start) * 1000.0)
