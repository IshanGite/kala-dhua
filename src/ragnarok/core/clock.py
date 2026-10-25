"""One Clock for the whole system. Nothing else may call time.time() for domain logic.

Storage is UTC milliseconds (D-20). Tests and the demo advance a SimClock instead of waiting.
"""

from __future__ import annotations

import time
from typing import Protocol

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


class Clock(Protocol):
    def now_ms(self) -> int: ...

    def monotonic(self) -> float: ...


class RealClock:
    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def monotonic(self) -> float:
        return time.monotonic()


class SimClock:
    """A clock that only moves when told to. Starts at a fixed instant so runs are reproducible."""

    def __init__(self, start_ms: int = 1_760_000_000_000) -> None:
        self._ms = start_ms
        self._mono = 0.0

    def now_ms(self) -> int:
        return self._ms

    def monotonic(self) -> float:
        return self._mono

    def advance(self, *, ms: int = 0, hours: float = 0, days: float = 0) -> int:
        delta = ms + int(hours * HOUR_MS) + int(days * DAY_MS)
        self._ms += delta
        self._mono += delta / 1000.0
        return self._ms


_current: Clock = RealClock()


def get_clock() -> Clock:
    return _current


def set_clock(clock: Clock) -> None:
    global _current
    _current = clock


def now_ms() -> int:
    return _current.now_ms()
