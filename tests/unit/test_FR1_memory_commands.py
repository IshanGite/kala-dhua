"""FR1.1 episodic recall, FR1.2 remembered preferences, FR1.3 daily maintenance wiring, NFR3.2 conversation restore."""

from __future__ import annotations

import pytest

from ragnarok.core.clock import RealClock, SimClock, set_clock
from ragnarok.daemon.chat import ChatService
from ragnarok.daemon.maintenance import Maintenance
from ragnarok.daemon.proactive import Inbox
from ragnarok.harness.memorycmd import MemoryCommands
from ragnarok.memory.models import EventIn
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.runtime_state import RuntimeState
from ragnarok.memory.service import MemoryService

T0 = 1_761_300_000_000


@pytest.fixture
def world(tmp_path, keys):
    clk = SimClock(T0)
    set_clock(clk)
    mem = MemoryService(tmp_path / "db" / "d.db", keys)
    reader = MemoryReader(mem.db_path, keys)
    cmd = MemoryCommands(mem, reader)
    yield mem, reader, cmd, clk, keys
    mem.close()
    set_clock(RealClock())


def say(mem, text, kind="user_message"):
    return mem.write_event(EventIn(compartment="work", kind=kind, actor="user", content=text))


def test_remember_a_preference_and_recall_it(world):
    mem, reader, cmd, *_ = world
    eid = say(mem, "remember that I prefer morning meetings")
    out = cmd.remember("remember that I prefer morning meetings", eid)
    assert out.startswith("Remembered: I prefer morning meetings")
    assert "morning meetings" in cmd.recall("what do you know about me")
    facts = reader.facts(reader.compartment_id("work"))
    assert facts[0]["kind"] == "preference" and facts[0]["trust"] == "user_direct" and reader.provenance_of("fact", facts[0]["id"])


def test_my_thing_is_an_attribute_and_a_new_value_supersedes_the_old_one_but_keeps_history(world):
    mem, reader, cmd, clk, _ = world
    cmd.remember("remember that my favourite colour is red", say(mem, "remember that my favourite colour is red"))
    clk.advance(days=1)
    r = cmd.remember("remember that my favourite colour is blue", say(mem, "remember that my favourite colour is blue"))
    assert "replaces" in r
    assert "blue" in cmd.recall("what is my favourite colour?")
    cid = reader.compartment_id("work")
    assert len(reader.facts(cid)) == 1 and len(reader.facts(cid, include_closed=True)) == 2, "the old value is closed, not erased"


def test_remember_to_do_something_is_a_reminder_not_a_fact(world):
    mem, _, cmd, *_ = world
    assert cmd.remember("remember to call mom tomorrow", say(mem, "remember to call mom tomorrow")) is None
    assert cmd.recall("what is the weather") is None


def test_episodic_history_lists_what_was_asked_and_done_today(world):
    mem, reader, cmd, clk, _ = world
    say(mem, "remind me to call mom tomorrow at 8am")
    say(mem, "remind me to call mom tomorrow at 8am -> succeeded", kind="task_result")
    out = cmd.history("what did I ask you today")
    assert "you asked: remind me to call mom" in out and "result:" in out
    assert "Nothing logged" in cmd.history("what did I ask you yesterday")
    assert cmd.history("how much is the invoice") is None


def test_the_chat_log_never_becomes_knowledge(world):
    mem, reader, _, *_ = world
    say(mem, "super secret zebra plan", kind="user_message")
    say(mem, "super secret zebra plan", kind="assistant_message")
    assert reader.fts_events("zebra", reader.compartment_id("work")) == []


def test_conversation_is_restored_after_a_restart(world):
    mem, reader, _, _, keys = world
    say(mem, "hello there")
    say(mem, "Hello! How can I help?", kind="assistant_message")
    say(mem, "what is on my calendar tomorrow")

    class A:
        pass

    a = A()
    a.mem, a.reader = mem, MemoryReader(mem.db_path, keys)
    a.ctx = type("C", (), {"compartment": "work"})()
    chat = ChatService(a)
    assert chat.restore() == 3 and chat.history[0] == ("user", "hello there") and chat.history[1][0] == "assistant"


def test_daily_maintenance_runs_once_a_day_and_asks_before_the_first_purge(world):
    mem, reader, cmd, clk, keys = world
    state = RuntimeState(mem)
    inbox = Inbox(state)
    m = Maintenance(mem, state, inbox)
    assert m.due() and m.run() != {} and not m.due() and m.run() == {}
    clk.advance(days=1, ms=1)
    assert m.due()
    assert set(m.last_report()) >= {"summaries", "archived", "purged"}
