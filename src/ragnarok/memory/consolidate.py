"""FR1.3 consolidation: roll old events up into one saga summary per week and compartment.

Idempotent (unique period per compartment), watermark-free because the period key is the watermark. The default summariser is
extractive and local; a gateway-backed one can be passed in and is validated by the caller.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ragnarok.core.clock import get_clock
from ragnarok.core.ids import new_id
from ragnarok.memory.service import MemoryService

DAY_MS = 86_400_000


def extractive(rows: list[dict[str, Any]]) -> str:
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    head = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
    firsts = " | ".join(r["content"].strip().splitlines()[0][:80] for r in rows[:3] if r["content"].strip())
    return f"{len(rows)} events ({head}). {firsts}"


def consolidate(mem: MemoryService, *, older_than_days: int = 14, summarize: Callable[[list[dict[str, Any]]], str] = extractive) -> int:
    cutoff = get_clock().now_ms() - older_than_days * DAY_MS

    def op(c: Any) -> int:
        made = 0
        rows = c.execute("SELECT compartment_id, ts, kind, content FROM events WHERE ts<? ORDER BY ts", (cutoff,)).fetchall()
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in rows:
            y, w, _ = time.gmtime(r["ts"] / 1000).tm_year, time.strftime("%W", time.gmtime(r["ts"] / 1000)), 0
            groups.setdefault((r["compartment_id"], f"{y}-W{w}"), []).append(dict(r))
        for (comp, period), evs in groups.items():
            if c.execute("SELECT 1 FROM saga WHERE compartment_id=? AND period=?", (comp, period)).fetchone():
                continue
            c.execute("INSERT INTO saga(id,compartment_id,period,summary) VALUES (?,?,?,?)", (new_id(), comp, period, summarize(evs)))
            made += 1
        return made

    return int(mem.with_tx(op))
