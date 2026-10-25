"""FR1.3 forgetting: score, archive, grace period, purge through the same cascade as a user delete.

S = w_r e^(-dt/tau) + w_a ln(1+n) + w_i I + w_c C. Pinned, user-edited and explicitly protected facts are never touched.
The first purge ever needs the user's confirmation; later purges run on their own and leave count-only tombstones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ragnarok.core.clock import get_clock
from ragnarok.memory.delete import Deleter, DeleteSelector
from ragnarok.memory.service import MemoryService

DAY_MS = 86_400_000
TAU_DAYS = 60.0
W_RECENCY, W_ACCESS, W_IMPORTANCE, W_CONFIDENCE = 0.35, 0.15, 0.30, 0.20
ARCHIVE_BELOW = 0.30
GRACE_DAYS = 30


def retention_score(*, age_days: float, access_count: int, importance: float, confidence: float) -> float:
    access = min(1.0, math.log1p(access_count) / math.log1p(20))
    return W_RECENCY * math.exp(-max(age_days, 0.0) / TAU_DAYS) + W_ACCESS * access + W_IMPORTANCE * importance + W_CONFIDENCE * confidence


@dataclass
class PruneReport:
    archived: int = 0
    restored: int = 0
    purged: int = 0
    awaiting_first_purge: int = 0
    protected: int = 0
    notes: list[str] = field(default_factory=list)


class Retention:
    def __init__(self, mem: MemoryService) -> None:
        self._m = mem

    def first_purge_confirmed(self) -> bool:
        return self._m.with_tx(lambda c: c.execute("SELECT 1 FROM prune_state WHERE key='first_purge_confirmed'").fetchone() is not None)

    def confirm_first_purge(self) -> None:
        self._m.with_tx(lambda c: c.execute("INSERT OR REPLACE INTO prune_state(key,value) VALUES ('first_purge_confirmed','1')"))

    def _protected(self, c: Any, extra: set[str]) -> set[str]:
        edited = {r[0] for r in c.execute("SELECT DISTINCT child_id FROM provenance WHERE child_type='fact' AND parent_type='user_edit'")}
        pinned = {r[0] for r in c.execute("SELECT id FROM facts WHERE pinned=1")}
        return edited | pinned | extra

    def run(self, *, protect: set[str] | None = None, allow_first_purge: bool = False) -> PruneReport:
        now = get_clock().now_ms()
        rep = PruneReport()

        def sweep(c: Any) -> list[str]:
            never = self._protected(c, protect or set())
            rep.protected = len(never)
            rows = c.execute("SELECT id, learned_at, last_accessed, access_count, importance, confidence, state FROM facts WHERE state IN ('active','archived')").fetchall()
            for r in rows:
                if r["id"] in never:
                    if r["state"] == "archived":
                        c.execute("UPDATE facts SET state='active', archived_at=NULL WHERE id=?", (r["id"],))
                        rep.restored += 1
                    continue
                age = (now - max(r["learned_at"], r["last_accessed"] or 0)) / DAY_MS
                s = retention_score(age_days=age, access_count=r["access_count"], importance=r["importance"], confidence=r["confidence"])
                if r["state"] == "active" and s < ARCHIVE_BELOW:
                    c.execute("UPDATE facts SET state='archived', archived_at=? WHERE id=?", (now, r["id"]))
                    rep.archived += 1
                elif r["state"] == "archived" and s >= ARCHIVE_BELOW + 0.1:
                    c.execute("UPDATE facts SET state='active', archived_at=NULL WHERE id=?", (r["id"],))  # used again: it comes back
                    rep.restored += 1
            return [x[0] for x in c.execute("SELECT id FROM facts WHERE state='archived' AND archived_at<=?", (now - GRACE_DAYS * DAY_MS,))]

        expired = self._m.with_tx(sweep)
        if not expired:
            return rep
        if not (allow_first_purge or self.first_purge_confirmed()):
            rep.awaiting_first_purge = len(expired)
            rep.notes.append("first purge needs the user's confirmation")
            return rep
        d = Deleter(self._m)
        sel = DeleteSelector(fact_ids=tuple(expired))
        receipt = d.delete(sel, d.preview(sel).confirm_token)
        rep.purged = receipt.counts["facts"]
        if allow_first_purge:
            self.confirm_first_purge()
        return rep
