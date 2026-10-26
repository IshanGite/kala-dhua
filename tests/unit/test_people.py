"""Relational memory: one person per handle, the same full name on another app joins them, a bare first name never does, and a delete leaves no names behind."""

from __future__ import annotations

import pytest

from ragnarok.core.clock import RealClock, SimClock, set_clock
from ragnarok.memory.delete import Deleter, DeleteSelector
from ragnarok.memory.models import CanonicalItem, ChunkIn
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.service import MemoryService


@pytest.fixture
def world(tmp_path, keys):
    set_clock(SimClock(1_760_000_000_000))
    mem = MemoryService(tmp_path / "db" / "d.db", keys)
    yield mem, keys
    mem.close()
    set_clock(RealClock())


def put(mem, source, nid, author, text="hello there friend"):
    return mem.write_item(CanonicalItem(source=source, native_id=nid, compartment="work", trust="received", title="t", chunks=[ChunkIn(text=text)], flags={"author": author})).item_id


def test_same_full_name_on_two_apps_is_one_person_and_a_first_name_is_not_merged(world):
    mem, keys = world
    put(mem, "slack", "s1", "Ravi Kumar")
    put(mem, "gmail", "g1", "Ravi Kumar <ravi@example.com>")
    put(mem, "telegram", "t1", "Ravi")
    put(mem, "discord", "d1", "Ravi")
    people = MemoryReader(mem.db_path, keys).people_list()
    names = sorted(p["name"] for p in people)
    assert names == ["Ravi", "Ravi", "Ravi Kumar"]  # the two single-name Ravis stay apart
    full = next(p for p in people if p["name"] == "Ravi Kumar")
    assert {(i["platform"], i["handle"]) for i in full["identities"]} == {("slack", "ravi kumar"), ("gmail", "ravi@example.com")}


def test_writing_the_same_message_again_does_not_duplicate(world):
    mem, keys = world
    put(mem, "slack", "s1", "Asha Rao")
    put(mem, "slack", "s2", "Asha Rao", text="another one")
    assert len(MemoryReader(mem.db_path, keys).people_list()) == 1


def test_delete_removes_the_names_of_people_who_no_longer_appear(world):
    mem, keys = world
    a = put(mem, "slack", "s1", "Ravi Kumar", text="the Zephyrine acquisition closes in March")
    put(mem, "gmail", "g1", "Meera Shah <meera@example.com>", text="lunch on friday")
    d = Deleter(mem)
    sel = DeleteSelector(item_ids=(a,))
    d.delete(sel, d.preview(sel).confirm_token)
    names = [p["name"] for p in MemoryReader(mem.db_path, keys).people_list()]
    assert names == ["Meera Shah"]
    sel2 = DeleteSelector(source="gmail")
    d.delete(sel2, d.preview(sel2).confirm_token)
    assert MemoryReader(mem.db_path, keys).people_list() == []


def test_backfill_notes_people_from_older_items(world):
    mem, keys = world
    put(mem, "slack", "s1", "Nina Verma")
    mem._w.execute("DELETE FROM identities")
    mem._w.execute("DELETE FROM people")
    assert MemoryReader(mem.db_path, keys).people_list() == []
    assert mem.backfill_people() == 1
    assert [p["name"] for p in MemoryReader(mem.db_path, keys).people_list()] == ["Nina Verma"]


def test_meta_memory_counts_calls_and_failures_and_keeps_only_numbers(world):
    mem, keys = world
    mem.note_meta("action", "rule", True, [("reminder.add", True)])
    mem.note_meta("action", "rule", False, [("reminder.add", False), ("mail.send", True)])
    r = MemoryReader(mem.db_path, keys)
    assert r.skill_stats() == {"reminder.add": {"calls": 2, "failures": 1}, "mail.send": {"calls": 1, "failures": 0}}
    assert r.router_stats() == [{"decision": "action", "backend": "rule", "n": 2, "correct": 1}]
