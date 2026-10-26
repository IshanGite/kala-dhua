"""180 simulated days of ingest and use with the retention policy (FR1.3). A simulated clock moves day by day; facts that are used stay, unused low-value facts are archived then purged, pinned facts never go."""
from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path

from evals.common import lat, prop

SUITE = "forgetting"
REQ = "FR1.3 forgetting and bounded growth"


def run() -> dict:
    from ragnarok.core import clock as ck
    from ragnarok.memory.models import EventIn, FactIn, ProvenanceIn
    from ragnarok.memory.retention import Retention
    from ragnarok.memory.service import MemoryService
    from ragnarok.store.crypto import KeyHierarchy

    sim = ck.SimClock()
    old = ck.get_clock()
    ck.set_clock(sim)
    try:
        mem = MemoryService(Path(tempfile.mkdtemp()) / "db" / "d.db", KeyHierarchy(bytes(range(32))), sim)
        rng = random.Random(11)
        ret = Retention(mem)
        ret.confirm_first_purge()
        sizes, used_ids, unused_ids, pinned, snaps = [], [], [], [], {}
        for day in range(180):
            for i in range(10):  # ten new facts a day
                important = rng.random() < 0.2
                subj = f"s{day}-{i}"
                ev = mem.write_event(EventIn(compartment="work", kind="note", actor="user", content=f"{subj} detail text"))
                r = mem.write_fact(FactIn(compartment="work", kind="knowledge", subject=subj, predicate="note", value=f"{subj} detail text", confidence=0.9 if important else 0.5,
                                          importance=0.9 if important else 0.2, trust="user_file"), [ProvenanceIn(parent_type="event", parent_id=ev, quote=f"{subj} detail text")])
                if r.id is None:
                    continue
                (used_ids if i < 2 else unused_ids).append((r.id, day))
                if day < 5 and i == 9:
                    mem.with_tx(lambda c, f=r.id: c.execute("UPDATE facts SET pinned=1 WHERE id=?", (f,)))
                    pinned.append(r.id)
            used_today = [f for f, d in used_ids if sim.now_ms() - 0 and rng.random() < 0.15]  # the person keeps coming back to a few facts
            if used_today:
                mem.bump_access(used_today)
            sim.advance(days=1)
            if day % 7 == 6:
                ret.run()
            sizes.append(mem.with_tx(lambda c: c.execute("SELECT COUNT(*) FROM facts WHERE state IN ('active','archived')").fetchone()[0]))
            if day in (89, 179):
                snaps[day + 1] = {st: mem.with_tx(lambda c, st=st: c.execute("SELECT COUNT(*) FROM facts WHERE state=?", (st,)).fetchone()[0]) for st in ("active", "archived")}
        def state_of(f: str) -> str | None:
            r = mem.with_tx(lambda c: c.execute("SELECT state FROM facts WHERE id=?", (f,)).fetchone())
            return r[0] if r else None
        def alive(ids):
            return sum(1 for f, _ in ids if state_of(f) == "active")
        old_used = [x for x in used_ids if x[1] < 90]
        old_unused = [x for x in unused_ids if x[1] < 90]
        pin_alive = sum(1 for f in pinned if state_of(f) == "active")
        ts = []
        for _ in range(30):
            t = time.perf_counter()
            mem.with_tx(lambda c: c.execute("SELECT id FROM facts WHERE state='active' LIMIT 50").fetchall())
            ts.append((time.perf_counter() - t) * 1000)
        total = len(used_ids) + len(unused_ids)
        return {"suite": SUITE, "requirement": REQ, "n": total,
                "metrics": {"facts_written": total, "state_at_day_90": snaps[90], "state_at_day_180": snaps[180], "stored_fraction_of_written_day_180": round(sizes[-1] / max(total, 1), 3),
                            "growth_per_day_days_150_to_180": round((sizes[-1] - sizes[-31]) / 30, 2), "write_rate_per_day": 10,
                            "frequently_used_facts_older_than_90d_still_active": prop(alive(old_used), len(old_used)),
                            "rarely_used_facts_older_than_90d_still_active": prop(alive(old_unused), len(old_unused)),
                            "pinned_facts_kept": prop(pin_alive, len(pinned)), "plain_read_of_50_active_facts_ms": lat(ts)},
                "series_stored_facts_every_5_days": sizes[::5],
                "notes": "Simulated clock, 10 facts per day for 180 days (20% important); each day a random 15% of the 'first two facts of each earlier day' are used again. Retention sweep weekly, 30-day grace before a purge. Measures the policy, not model quality. 'Stored' counts active plus archived; the difference to facts written was purged."}
    finally:
        ck.set_clock(old)
