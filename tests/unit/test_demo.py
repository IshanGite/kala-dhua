"""Demo mode: synthetic data for every app, every send is a local fake, new messages can arrive on demand."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ragnarok.core.clock import RealClock, SimClock, set_clock
from ragnarok.daemon.demo import INCOMING, SEED, DemoState, make_senders
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.service import MemoryService
from ragnarok.store.vectors import VectorIndex

NOW = 1_761_300_000_000


@pytest.fixture
def demo(tmp_path, keys):
    set_clock(SimClock(NOW))
    mem = MemoryService(tmp_path / "db" / "d.db", keys)
    st = DemoState(world=SimpleNamespace(mem=mem, gateway=FakeGateway(), index=VectorIndex()))
    yield st, MemoryReader(mem.db_path, keys)
    mem.close()
    set_clock(RealClock())


@pytest.mark.asyncio
async def test_every_connected_app_has_demo_data(demo):
    st, r = demo
    await st.seed()
    by = dict(r._r.execute("SELECT source, count(*) FROM items GROUP BY source").fetchall())
    assert {"gmail", "slack", "discord", "telegram", "gcal", "gdrive", "gmeet", "notifications"} <= set(by)
    assert sum(by.values()) == len({e["n"] for e in SEED})


@pytest.mark.asyncio
async def test_incoming_messages_arrive_and_cycle(demo):
    st, r = demo
    await st.seed()
    n0 = r.count("items")
    got = [await st.incoming() for _ in range(len(INCOMING) + 1)]
    assert r.count("items") == n0 + len(INCOMING) + 1 and got[0]["source"] == got[len(INCOMING)]["source"]


@pytest.mark.asyncio
async def test_sends_are_recorded_not_sent(demo):
    st, _ = demo
    s = make_senders(st)
    await s["mail"]({"to": "a@b.co", "subject": "Hi", "body": "Body"})
    await s["telegram"]("T01", "hello")
    assert [o["kind"] for o in st.outbox] == ["telegram", "email"]
    assert set(s) >= {"mail", "mail_reply", "gmail_draft", "calendar", "drive", "telegram", "discord", "slack"}


def test_demo_contains_a_hostile_message_for_the_injection_demo():
    assert any("Ignore all previous instructions" in e["x"] for e in SEED)
