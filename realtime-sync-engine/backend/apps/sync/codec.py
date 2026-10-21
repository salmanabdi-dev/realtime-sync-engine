"""JSON encoding for the wire protocol. orjson is ~5-10x faster than the stdlib
encoder; the fallback keeps pure-logic tests runnable without it."""

from __future__ import annotations

import json
from typing import Any

try:  # pragma: no cover - exercised implicitly
    import orjson

    def dumps(obj: Any) -> str:
        return orjson.dumps(obj).decode()

    def loads(data: str | bytes) -> Any:
        return orjson.loads(data)

except ImportError:  # pragma: no cover

    def dumps(obj: Any) -> str:
        return json.dumps(obj, separators=(",", ":"), default=str)

    def loads(data: str | bytes) -> Any:
        return json.loads(data)
