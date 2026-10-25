"""Tier-2 extraction: dates, amounts, addresses and links by patterns. No model call, always runs (ARCHITECTURE 8.4)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december"
_DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\.?,?\s+\d{{4}}\b", re.I),
    re.compile(rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\b", re.I),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
]
_AMOUNT = re.compile(r"(?:(?:INR|Rs\.?|USD|EUR|GBP)\s?|[₹$€£]\s?)\d[\d,]*(?:\.\d{1,2})?(?:\s?(?:lakh|crore|k|m|million|billion))?", re.I)
_URL = re.compile(r"https?://[^\s<>\"')]+")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_DEADLINE = re.compile(r"(?i)\b(?:due|deadline|by|before|until|expires?|renews?|renewal)\b[^.\n]{0,40}")


@dataclass(frozen=True)
class Extracted:
    kind: str  # date | amount | url | email | deadline_phrase
    value: str
    start: int
    end: int


def extract(text: str, limit: int = 40) -> list[Extracted]:
    found: list[Extracted] = []
    for pat in _DATE_PATTERNS:
        found += [Extracted("date", m.group(0), m.start(), m.end()) for m in pat.finditer(text)]
    found += [Extracted("amount", m.group(0).strip(), m.start(), m.end()) for m in _AMOUNT.finditer(text)]
    found += [Extracted("url", m.group(0), m.start(), m.end()) for m in _URL.finditer(text)]
    found += [Extracted("email", m.group(0).lower(), m.start(), m.end()) for m in _EMAIL.finditer(text)]
    found += [Extracted("deadline_phrase", m.group(0).strip(), m.start(), m.end()) for m in _DEADLINE.finditer(text)]
    seen, out = set(), []
    for e in sorted(found, key=lambda e: e.start):
        key = (e.kind, e.value.lower())
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out[:limit]


def summarize(extracted: list[Extracted]) -> dict[str, list[str]]:
    """Compact form stored in an item's flags: kinds to distinct values, no positions."""
    out: dict[str, list[str]] = {}
    for e in extracted:
        out.setdefault(e.kind, []).append(e.value)
    return out
