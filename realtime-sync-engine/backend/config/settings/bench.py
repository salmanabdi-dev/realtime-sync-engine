"""Benchmark settings: production-like. DEBUG off (it records every query),
quiet logging, metrics on. Durability settings are untouched on purpose."""

from .base import *  # noqa: F401,F403
from .base import SYNC

DEBUG = False
ALLOWED_HOSTS = ["*"]
LOG_LEVEL = "WARNING"
SYNC["METRICS_ENABLED"] = True
SYNC["RATE_LIMIT_PER_SECOND"] = 100_000
