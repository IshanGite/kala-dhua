"""Autonomy modes per connector (D-13): observe, draft (default), act_limited. External writes ALWAYS need a click."""

from __future__ import annotations

from typing import Literal

Mode = Literal["observe", "draft", "act_limited"]
DEFAULT: Mode = "draft"


def mode_allows(mode: str, tier: int, *, preparing: bool = False) -> bool:
    """May this tier run at all in this mode? `preparing` is the side-effect-free prepare step of a write."""
    if mode == "observe":
        return tier == 0
    if tier <= 1:
        return True
    return preparing  # tier >= 2: only the local prepare step runs without a click; commit needs approval


def commit_requires_click(tier: int) -> bool:
    return tier >= 2
