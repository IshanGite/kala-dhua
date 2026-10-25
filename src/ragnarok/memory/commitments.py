"""Reminders and to-dos (the `commitments` table). SQL lives under memory/ by rule."""

from __future__ import annotations

from typing import Any

from ragnarok.core.clock import get_clock
from ragnarok.core.ids import new_id
from ragnarok.memory.service import MemoryService


class Commitments:
    def __init__(self, mem: MemoryService) -> None:
        self._m = mem

    def add(self, compartment: str, what: str, due_at: int | None, trust: str = "user_direct") -> str:
        cid = self._m.ensure_compartment(compartment)
        rid = new_id()
        self._m.with_tx(lambda c: c.execute("INSERT INTO commitments(id,compartment_id,what,due_at,state,trust) VALUES (?,?,?,?, 'open', ?)", (rid, cid, what, due_at, trust)))
        return rid

    def remove(self, rid: str) -> None:
        self._m.with_tx(lambda c: c.execute("DELETE FROM commitments WHERE id=?", (rid,)))

    def list_open(self) -> list[dict[str, Any]]:
        return self._m.with_tx(lambda c: [dict(r) for r in c.execute("SELECT id, what, due_at FROM commitments WHERE state='open' ORDER BY coalesce(due_at, 9e18)")])

    def fire_due(self, now_ms: int | None = None) -> list[dict[str, Any]]:
        """Reminders whose time has come: returned once and marked done, so a restart never repeats one."""
        now = now_ms if now_ms is not None else get_clock().now_ms()

        def op(c: Any) -> list[dict[str, Any]]:
            rows = [dict(r) for r in c.execute("SELECT id, what, due_at FROM commitments WHERE state='open' AND due_at IS NOT NULL AND due_at<=? ORDER BY due_at", (now,))]
            for r in rows:
                c.execute("UPDATE commitments SET state='done' WHERE id=?", (r["id"],))
            return rows

        return self._m.with_tx(op)  # type: ignore[no-any-return]
