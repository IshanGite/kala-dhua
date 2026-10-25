"""Structured logging that can never carry content.

Only codes, ids and numbers are accepted. A string longer than MAX_FIELD or containing whitespace
runs that look like prose is refused, so a developer cannot accidentally log an email body.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any

MAX_FIELD = 64
_lock = threading.Lock()
_sink: Any = None  # None means "whatever sys.stderr is right now" (so test capture and redirection work)


class ContentInLogError(ValueError):
    pass


def _check(key: str, value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        if len(value) > MAX_FIELD or value.count(" ") > 2:
            raise ContentInLogError(f"log field {key!r} looks like content")
        return value
    if isinstance(value, (list, tuple)):
        return [_check(key, v) for v in value][:20]
    raise ContentInLogError(f"log field {key!r} has unsupported type {type(value).__name__}")


def log(code: str, **fields: Any) -> None:
    """Emit one JSON line: {"code": "...", ...ids and numbers}."""
    record = {"code": code, **{k: _check(k, v) for k, v in fields.items()}}
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    with _lock:
        print(line, file=_sink or sys.stderr, flush=True)


def set_sink(stream: Any) -> None:
    global _sink
    _sink = stream
