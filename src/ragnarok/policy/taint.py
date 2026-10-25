"""Taint tracking: every value carries the lowest trust among its ancestors (ARCHITECTURE 5.2 item 1, CaMeL-lite 5.4)."""

from __future__ import annotations

from collections.abc import Iterable

from ragnarok.memory.models import LOW_TRUST, TRUST_RANK, min_trust

__all__ = ["LOW_TRUST", "is_tainted", "lowest", "min_trust"]


def lowest(levels: Iterable[str]) -> str:
    items = list(levels)
    return min_trust(*items) if items else "user_direct"


def is_tainted(trust: str) -> bool:
    """True when the data came from outside the user's own hand (received mail/chat or the web)."""
    return trust in LOW_TRUST or TRUST_RANK.get(trust, 0) <= TRUST_RANK["received"]
