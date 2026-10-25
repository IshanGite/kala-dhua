"""Idle-time dreaming: only when plugged in and idle; replays the day, consolidates, rehearses what failed (dry run), drafts skill improvements; never acts outside the computer; stops when the person returns."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ragnarok.core.clock import RealClock, get_clock, set_clock
from ragnarok.daemon.wiring import build_world, warm_up
from ragnarok.llm_gateway.fake import DIM, FakeGateway
from ragnarok.memory.models import EventIn
from ragnarok.platform.secrets import MemoryStore

SENT: list = []


async def fake_send(*a, **k):
    SENT.append(a)
    return "x"


class Power:
    def __init__(self, ac=True, idle=7200.0):
        self.ac, self.idle = ac, idle

    def on_ac(self):
        return self.ac

    def idle_seconds(self):
        return self.idle


def responder(messages, schema):
    props = (schema or {}).get("properties", {})
    if "summary" in props and "open_loops" in props:
        return {"summary": "You asked about the budget and set a reminder.", "topics": ["budget"], "open_loops": ["send the contract"], "preferences": ["morning meetings"]}
    if "instructions" in props and "name" in props:
        return {"name": "budget-checker", "description": "Checks the budget numbers you paste.", "triggers": ["check this budget", "review these budget numbers", "is this budget right"], "instructions": "Check the numbers and list anything odd.",
                "tools": [], "examples": [{"input": "Budget: rent 10, food 5, total 20", "contains": ["total"]}]}
    if schema is None:
        return "The total looks wrong."
    return {}


@pytest.fixture
def world(tmp_path, monkeypatch):
    set_clock(RealClock())
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "home"))
    SENT.clear()
    w = build_world(vault=MemoryStore(), gateway=FakeGateway(responder), sandbox=False, use_laya=False, embed_rungs=False, senders={"telegram": fake_send, "mail": fake_send, "calendar": fake_send, "drive": fake_send}, max_cpu=101)
    d = w.services.dreamer
    d._power = Power()
    d.last_activity = get_clock().now_ms() - 3 * 3_600_000
    yield w, d
    w.mem.close()


def say(w, user, assistant=None):
    w.mem.write_event(EventIn(compartment="work", kind="user_message", actor="user", content=user))
    if assistant:
        w.mem.write_event(EventIn(compartment="work", kind="assistant_message", actor="agent", content=assistant, trust="agent"))


# ------------------------------------------------------------------ when it dreams
def test_it_waits_for_the_charger(world):
    _, d = world
    d._power = Power(ac=False)
    assert d.eligibility() == (False, "waiting for the charger")
    d.configure(battery_ok=True)
    assert d.eligibility()[0] is True


def test_it_waits_until_you_have_been_away(world):
    _, d = world
    d.notify_activity()
    ok, why = d.eligibility()
    assert not ok and "away" in why
    d.last_activity = get_clock().now_ms() - 3_600_000
    d._power = Power(idle=30.0)  # the keyboard was touched half a minute ago
    d._cache = (0.0, None)
    assert not d.eligibility()[0]


def test_unknown_power_state_does_not_dream(world):
    _, d = world
    d._power = Power(ac=None)
    assert d.eligibility() == (False, "cannot tell whether the computer is plugged in")


def test_switched_off_and_recently_dreamed(world):
    w, d = world
    d.configure(enabled=False)
    assert d.eligibility() == (False, "dreaming is switched off")
    d.configure(enabled=True)
    w.services.runtime.state.set("dream_last", str(get_clock().now_ms() - 3600_000))
    assert "recently" in d.eligibility()[1]
    assert d.eligibility(force=True)[0]  # the person can ask for it


# ------------------------------------------------------------------ what it does
async def test_replay_writes_a_day_summary_into_memory_and_a_report_to_the_inbox(world):
    w, d = world
    await warm_up(w)
    say(w, "what is the budget review about", "It is on Thursday.")
    say(w, "remind me to send the contract", "Reminder set.")
    rep = await d.run(force=True)
    assert rep["phases"]["replay"]["messages"] == 2 and "budget" in rep["phases"]["replay"]["summary"]
    row = w.reader._r.execute("SELECT title FROM items WHERE source='dream'").fetchone()
    assert row and row[0].startswith("Day summary")
    items = w.services.proactive.inbox.items()
    assert any(i["title"].startswith("Dream report") and "Still open: send the contract" in i["body"] for i in items)


async def test_a_dream_never_sends_or_changes_anything_outside(world, monkeypatch):
    w, d = world
    await warm_up(w)
    say(w, "tell Ravi on telegram that I am late", "Message posted to Ravi.")
    called = []

    async def boom(*a, **k):
        called.append(1)
        raise AssertionError("a dream must never execute a task")

    monkeypatch.setattr(w.agent.executor, "run_task", boom)
    monkeypatch.setattr(w.agent.executor, "start_task", boom)
    await d.run(force=True)
    assert SENT == [] and called == []
    assert not any(hasattr(d, a) for a in ("executor", "senders", "actions", "broker", "hub"))  # no way to act: structurally


async def test_rehearsal_replays_failed_tasks_and_misunderstood_requests_as_a_dry_run(world):
    w, d = world
    await warm_up(w)
    say(w, "ping Priya about lunch", "I did not do anything. That sounds like a request to act, but I could not tell which app.")
    tid = w.ledger.create_task("work", "email Dev the invoice", "user", "t1")
    w.ledger.set_task_state(tid, "failed")
    seen = []

    async def rehearse(text):
        seen.append(text)
        return ("Priya" in text), "would plan: chat.reply" if "Priya" in text else "still not clear"

    d._rehearse = rehearse
    rep = await d.run(force=True)
    rh = rep["phases"]["rehearse"]
    assert set(seen) == {"ping Priya about lunch", "email Dev the invoice"} and rh["rehearsed"] == 2 and rh["would_work_now"] == 1
    assert rh["still_failing"][0]["text"].startswith("email Dev")


async def test_it_stops_the_moment_you_come_back(world):
    w, d = world
    await warm_up(w)
    say(w, "ping Priya about lunch", "I did not do anything.")

    async def rehearse(text):
        d.notify_activity()  # the person sat down in the middle of the dream
        return False, "x"

    d._rehearse = rehearse
    rep = await d.run(force=True)
    assert rep["stopped_early"] and "scrolls" not in rep["phases"]


async def test_it_stops_when_unplugged(world):
    w, d = world
    await warm_up(w)
    d._forced = False
    d._power = Power(ac=False)
    rep = await d.run()  # not forced and not plugged in: it does not even start
    assert rep == {"skipped": "waiting for the charger"}


async def test_time_budget(world, monkeypatch):
    w, d = world
    monkeypatch.setattr("ragnarok.daemon.dream.BUDGET_S", -1.0)
    rep = await d.run(force=True)
    assert rep["stopped_early"] and rep["phases"] == {}


# ------------------------------------------------------------------ skills
GOOD = """---
name: budget-checker
description: Checks budget numbers and lists anything odd.
triggers: ["check this budget"]
tools: []
examples:
  - input: "Budget: rent 10, food 5, total 20"
    contains: ["wrong-word"]
---
Check the numbers. List anything odd.
"""


async def test_a_failing_scroll_gets_a_revision_draft_only_if_it_passes_more_of_its_own_tests(world, monkeypatch):
    w, d = world
    await warm_up(w)
    lib = d._runner.lib
    lib.write_user_scroll(GOOD)
    lib.approve("budget-checker", lib.peek("budget-checker").sha)
    from ragnarok.scrolls import author

    # the revision does not pass more tests than the original: dropped
    assert await author.propose_revision(d._gw, d._runner, "budget-checker") is None and lib.drafts() == []

    # a revision whose output satisfies the example is proposed, with a diff, and nothing in use changes
    d._gw.responder = lambda m, s: {"instructions": "Check the numbers and say wrong-word if the total does not add up."} if s and "instructions" in s.get("properties", {}) else "wrong-word total"
    cur_sha = lib.peek("budget-checker").sha
    draft = await author.propose_revision(d._gw, d._runner, "budget-checker")
    # the current version already passes now (the model says wrong-word), so there is nothing to improve
    assert draft is None and lib.peek("budget-checker").sha == cur_sha


async def test_new_scroll_is_drafted_for_a_habit_and_waits_for_approval(world, monkeypatch):
    w, d = world
    await warm_up(w)

    async def embed(texts, **kw):
        out = []
        for t in texts:
            v = np.full(DIM, 0.001, dtype=np.float32)
            v[0 if "budget" in t.lower() else 1] = 1.0
            out.append(v)
        return np.stack(out)

    d._gw.embed = embed
    for i in range(4):
        say(w, f"please check this budget sheet number {i}: rent food travel and the total")
    rep = await d.run(force=True)
    drafts = rep["phases"]["scrolls"]["drafts"]
    assert [x["name"] for x in drafts] == ["budget-checker"] and d._runner.lib.peek("budget-checker") is None  # a draft, not a scroll
    assert any("New scroll proposed: budget-checker" in i["title"] for i in w.services.proactive.inbox.items())
    assert d._runner.lib.drafts()[0]["new"] is True


# ------------------------------------------------------------------ settings and report
def test_settings_are_remembered(world):
    w, d = world
    d.configure(enabled=False, idle_min=25, battery_ok=True)
    assert d.settings() == {"enabled": False, "idle_min": 25, "battery_ok": True}
    st = d.status()
    assert st["plugged_in"] is True and st["running"] is False


async def test_the_last_report_is_kept_for_the_window(world):
    w, d = world
    await d.run(force=True)
    st = d.status()
    assert st["last_at"] and "phases" in st["report"] and json.dumps(st["report"])


async def test_a_better_revision_is_proposed_as_a_diff_and_only_applied_on_approval(world):
    w, d = world
    await warm_up(w)
    lib = d._runner.lib
    lib.write_user_scroll(GOOD)
    lib.approve("budget-checker", lib.peek("budget-checker").sha)
    old_sha = lib.peek("budget-checker").sha

    def smart(messages, schema):
        if schema and "instructions" in schema.get("properties", {}):
            return {"instructions": "Check the numbers and always write the word wrong-word when the total does not add up."}
        return "wrong-word, the total is off." if "wrong-word" in messages[0]["content"] else "Looks fine to me."

    d._gw.responder = smart
    from ragnarok.scrolls import author

    draft = await author.propose_revision(d._gw, d._runner, "budget-checker")
    assert draft and "wrong-word" in draft["diff"] and "2 of 2" not in draft["reason"] and "passes 1 of 1" in draft["reason"]
    assert lib.peek("budget-checker").sha == old_sha, "the scroll in use is untouched until a human approves the draft"
    assert lib.approve_draft(draft["id"]) == "budget-checker" and lib.peek("budget-checker").sha != old_sha
