"""Pure text-operation logic. No Django, no I/O.

An operation list is applied sequentially: each op's position refers to the text
as it stands after the previous op. Positions and lengths count Unicode code
points, which is what Python's str indexing uses. (The TypeScript client converts
from UTF-16 code units at its boundary, so emoji never desynchronize the sides.)

    {"kind": "insert", "pos": 5, "text": "X"}
    {"kind": "delete", "pos": 2, "length": 3}
"""

from __future__ import annotations

from collections.abc import Iterable


class InvalidOperation(ValueError):
    """An operation that cannot be applied to the current text."""


def apply_operation(content: str, op: dict) -> str:
    kind = op["kind"]
    pos = op["pos"]
    size = len(content)
    if kind == "insert":
        if pos > size:
            raise InvalidOperation(f"insert position {pos} is past the end of the text ({size})")
        return content[:pos] + op["text"] + content[pos:]
    if kind == "delete":
        end = pos + op["length"]
        if end > size:
            raise InvalidOperation(f"delete range {pos}..{end} is past the end of the text ({size})")
        return content[:pos] + content[end:]
    raise InvalidOperation(f"unknown operation kind {kind!r}")


def apply_operations(content: str, ops: Iterable[dict], max_chars: int | None = None) -> str:
    for op in ops:
        content = apply_operation(content, op)
    if max_chars is not None and len(content) > max_chars:
        raise InvalidOperation(f"document would exceed the {max_chars}-character limit")
    return content
