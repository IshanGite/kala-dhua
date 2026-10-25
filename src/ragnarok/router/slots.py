"""Deterministic slot extractors: turn the words of a request into typed parameter values WITHOUT a model (L0 route filling)."""

from __future__ import annotations

import re
from collections.abc import Callable

from ragnarok.router.analysis import parse_time_range

_TIME12 = re.compile(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b", re.I)
_TIME24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_NUMBER = re.compile(r"\b\d+\b")
_QUOTED = re.compile(r"[\"“']([^\"”']{2,120})[\"”']")
_PATH = re.compile(r"\b[\w./-]+\.[A-Za-z0-9]{1,5}\b")


def extract_time(text: str, now_ms: int = 0) -> tuple[str, str] | None:
    m = _TIME12.search(text)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
        h = h % 12 + (12 if ap == "pm" else 0)
        return f"{h:02d}:{mi:02d}", m.group(0)
    m = _TIME24.search(text)
    return (f"{int(m.group(1)):02d}:{m.group(2)}", m.group(0)) if m else None


def extract_email(text: str, now_ms: int = 0) -> tuple[str, str] | None:
    m = _EMAIL.search(text)
    return (m.group(0).lower(), m.group(0)) if m else None


def extract_number(text: str, now_ms: int = 0) -> tuple[str, str] | None:
    m = _NUMBER.search(text)
    return (m.group(0), m.group(0)) if m else None


def extract_quoted(text: str, now_ms: int = 0) -> tuple[str, str] | None:
    m = _QUOTED.search(text)
    return (m.group(1), m.group(0)) if m else None


def extract_path(text: str, now_ms: int = 0) -> tuple[str, str] | None:
    m = _PATH.search(text)
    return (m.group(0), m.group(0)) if m and "@" not in m.group(0) else None


def extract_date_range(text: str, now_ms: int = 0) -> tuple[str, str] | None:
    r = parse_time_range(text, now_ms)
    if not r:
        return None
    m = re.search(r"(yesterday|today|this week|last week|this month|last month|\d+ days? ago|last \w+day|on \w+day|in \w+)", text, re.I)
    return (f"{r[0]},{r[1]}", m.group(0) if m else "")


EXTRACTORS: dict[str, Callable[[str, int], tuple[str, str] | None]] = {
    "time": extract_time, "email": extract_email, "number": extract_number, "quoted": extract_quoted, "path": extract_path, "date_range": extract_date_range,
}


def fill(kind: str, text: str, now_ms: int) -> str | None:
    fn = EXTRACTORS.get(kind)
    got = fn(text, now_ms) if fn else None
    return got[0] if got else None


def detect(text: str, now_ms: int = 0) -> list[tuple[str, str, str]]:
    """Every typed value found in the request: (kind, normalized value, raw text as written)."""
    out = []
    for kind, fn in EXTRACTORS.items():
        if kind == "number":  # too eager for automatic templating (it would rewrite digits inside ids and times)
            continue
        got = fn(text, now_ms)
        if got and got[1]:
            out.append((kind, got[0], got[1]))
    return out


def signature(text: str, found: list[tuple[str, str, str]]) -> str:
    """The request with its concrete values replaced by <kind>, so 'move standup to 4pm' and '... to 5pm' look the same."""
    sig = text
    for kind, _value, raw in found:
        sig = sig.replace(raw, f"<{kind}>")
    return sig


def templatize(value, found: list[tuple[str, str, str]]):
    """Replace request-derived values inside parameters with {slot:kind}, including values embedded in longer strings."""
    if isinstance(value, str):
        out = value
        for kind, norm, raw in found:
            for token in {norm, raw}:
                if token and token in out:
                    out = out.replace(token, "{slot:" + kind + "}")
        return out
    if isinstance(value, dict):
        return {k: templatize(v, found) for k, v in value.items()}
    if isinstance(value, list):
        return [templatize(v, found) for v in value]
    return value


_SLOT = re.compile(r"\{slot:(\w+)\}")


def instantiate_value(value, text: str, now_ms: int):
    """Fill {slot:kind} placeholders from a new request. Returns (value, missing_kinds)."""
    missing: list[str] = []

    def walk(v):
        if isinstance(v, str):
            def sub(m):
                got = fill(m.group(1), text, now_ms)
                if got is None:
                    missing.append(m.group(1))
                    return m.group(0)
                return got

            return _SLOT.sub(sub, v)
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(value), missing
