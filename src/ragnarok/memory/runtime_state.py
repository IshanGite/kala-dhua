"""Small durable flags for the running agent (on/off toggle, last tick). SQL lives under memory/ by rule."""

from __future__ import annotations

from ragnarok.memory.service import MemoryService


class RuntimeState:
    def __init__(self, mem: MemoryService) -> None:
        self._m = mem

    def get(self, key: str, default: str = "") -> str:
        row = self._m.with_tx(lambda c: c.execute("SELECT value FROM prune_state WHERE key=?", (f"rt:{key}",)).fetchone())
        return str(row[0]) if row else default

    def set(self, key: str, value: str) -> None:
        self._m.with_tx(lambda c: c.execute("INSERT OR REPLACE INTO prune_state(key,value) VALUES (?,?)", (f"rt:{key}", value)))

    def held_senders(self) -> list[tuple[str, str]]:
        return self._m.with_tx(lambda c: [(r[0], r[1]) for r in c.execute("SELECT platform, handle FROM sender_pairing WHERE state='held' ORDER BY platform, handle")])
