"""FR1.3: the 1,000-event simulated-clock run, archive -> grace -> purge, never-prune list, first-purge confirmation."""

from __future__ import annotations

import pytest

from ragnarok.core.clock import RealClock, SimClock, set_clock
from ragnarok.memory.consolidate import consolidate
from ragnarok.memory.models import EventIn, FactIn, ProvenanceIn
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.retention import GRACE_DAYS, Retention, retention_score
from ragnarok.memory.service import MemoryService

START = 1_760_000_000_000


@pytest.fixture
def world(tmp_path, keys):
    clk = SimClock(START)
    set_clock(clk)
    mem = MemoryService(tmp_path / "db" / "d.db", keys)
    yield mem, keys, clk
    mem.close()
    set_clock(RealClock())


def fact(mem, subj, imp, conf=0.6):
    ev = mem.write_event(EventIn(compartment="work", kind="note", actor="user", content=f"{subj} detail text"))
    r = mem.write_fact(FactIn(compartment="work", kind="knowledge", subject=subj, predicate="note", value=f"{subj} detail text", confidence=conf, importance=imp, trust="user_file"),
                       [ProvenanceIn(parent_type="event", parent_id=ev, quote=f"{subj} detail text")])
    return r.id


def test_score_decays_with_age_and_rises_with_use_and_importance():
    base = dict(access_count=0, importance=0.5, confidence=0.6)
    assert retention_score(age_days=0, **base) > retention_score(age_days=200, **base)
    assert retention_score(age_days=100, access_count=15, importance=0.5, confidence=0.6) > retention_score(age_days=100, **base)
    assert retention_score(age_days=100, access_count=0, importance=0.9, confidence=0.6) > retention_score(age_days=100, **base)


def test_FR1_3_simulated_clock_1000_events(world):
    mem, keys, clk = world
    for i in range(1000):
        clk.advance(hours=2)
        mem.write_event(EventIn(compartment="work", kind="note" if i % 3 else "chat", actor="user", content=f"event {i} about project"))
    clk.advance(days=30)
    made = consolidate(mem, older_than_days=14)
    reader = MemoryReader(mem.db_path, keys)
    assert made > 5 and reader.count("saga") == made
    assert consolidate(mem, older_than_days=14) == 0, "idempotent: running it again changes nothing"
    assert reader.count("events") == 1000 and reader.verify_chain() == (True, None)


def test_archive_then_grace_then_purge_and_never_prune(world):
    mem, keys, clk = world
    stale, edited, pinned, used = fact(mem, "Stale", 0.1, 0.4), fact(mem, "Edited", 0.1, 0.4), fact(mem, "Pinned", 0.1, 0.4), fact(mem, "Used", 0.1, 0.4)
    mem.edit_fact(edited, "user fixed this")
    mem.with_tx(lambda c: c.execute("UPDATE facts SET pinned=1 WHERE id=?", (pinned,)))
    clk.advance(days=400)
    mem.bump_access([used] * 30)
    ret = Retention(mem)
    rep = ret.run()
    reader = MemoryReader(mem.db_path, keys)
    states = {r["id"]: r["state"] for r in reader._r.execute("SELECT id,state FROM facts")}
    assert states[stale] == "archived" and states[edited] == states[pinned] == "active"
    assert rep.archived >= 1 and rep.purged == 0
    clk.advance(days=GRACE_DAYS + 1)
    rep2 = ret.run()
    assert rep2.purged == 0 and rep2.awaiting_first_purge >= 1, "the very first purge asks the user"
    rep3 = ret.run(allow_first_purge=True)
    assert rep3.purged >= 1
    left = {r[0] for r in reader._r.execute("SELECT id FROM facts")}
    assert stale not in left and edited in left and pinned in left
    tomb = reader._r.execute("SELECT counts_json, scope FROM tombstones").fetchall()
    assert tomb and all("Stale" not in (t[0] + t[1]) for t in tomb), "tombstones hold counts only"
    clk.advance(ms=1)
    assert ret.first_purge_confirmed()
