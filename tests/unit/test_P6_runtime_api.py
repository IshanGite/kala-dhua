"""P6: the always-on runtime (toggle, continuous ingestion, activity feed, pairing) and the chat endpoint (grounded or honest 'I don't know')."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from ragnarok.connectors.base import Cursor, ItemRef, RawItem, SyncResult
from ragnarok.connectors.runner import ConnectorRunner
from ragnarok.core.clock import RealClock, set_clock
from ragnarok.daemon.api import Services, create_app
from ragnarok.daemon.auth import HEADER, TokenGuard
from ragnarok.daemon.runtime import Governor, Runtime
from ragnarok.harness.answer import NOTHING, Answerer
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.memory.queue import IngestQueue
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.runtime_state import RuntimeState
from ragnarok.memory.service import MemoryService
from ragnarok.store.vectors import VectorIndex


class Feed:
    name = "feed"

    def __init__(self):
        self.n = 0

    def describe(self):
        return None

    async def sync(self, cursor):
        self.n += 1
        return SyncResult([ItemRef(f"m{self.n}", {"sender": "U9"})], Cursor({"n": self.n}))

    async def fetch(self, ref):
        return RawItem("feed", ref.native_id, "The Orion launch moved to April because of the vendor delay.", "received")

    def actions(self):
        return []

    async def health(self):
        return None


@pytest.fixture
def world(tmp_path, keys):
    set_clock(RealClock())
    mem = MemoryService(tmp_path / "db" / "d.db", keys)
    gw, idx = FakeGateway(), VectorIndex()
    feed = Feed()
    runner = ConnectorRunner(mem, IngestQueue(mem), feed, "work", gateway=gw, index=idx)
    rt = Runtime(RuntimeState(mem), {"feed": runner}, interval_s=1.0, governor=Governor(max_cpu=101))
    reader = MemoryReader(mem.db_path, keys)
    yield mem, keys, rt, reader, gw, idx
    mem.close()


H = {"host": "127.0.0.1:8765"}


def client(world, answer_json=None):
    mem, keys, rt, reader, gw, idx = world
    if answer_json is not None:
        gw.responder = lambda messages, schema: answer_json
    g = TokenGuard("tok")
    app = create_app(g, 8765, Services(rt, Answerer(reader, idx, gw)))
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={HEADER: "tok"})


def test_toggle_stops_ingestion_and_is_remembered(world):
    mem, keys, rt, reader, *_ = world
    c = client(world)
    assert c.post("/tick").json()["sources"]["feed"]["ingested"] == 1
    assert c.post("/toggle", json={"enabled": False}).json()["enabled"] is False
    assert c.post("/tick").json()["sources"] == {}, "while off, nothing is read"
    assert reader.count("items") == 1
    assert Runtime(RuntimeState(mem), {}).enabled is False, "the switch survives a restart"
    c.post("/toggle", json={"enabled": True})
    assert c.post("/tick").json()["sources"]["feed"]["ingested"] == 1


def test_activity_feed_has_codes_and_counts_never_content(world):
    c = client(world)
    c.post("/tick")
    items = c.get("/activity").json()["items"]
    assert items and items[0]["source"] == "feed" and items[0]["ingested"] == 1
    assert "Orion" not in str(items)


async def test_background_loop_ingests_continuously(world):
    import asyncio

    mem, keys, rt, reader, *_ = world
    rt.interval = 0.05
    rt.start()
    await asyncio.sleep(0.6)
    await rt.stop()
    assert reader.count("items") >= 3


def test_chat_is_grounded_with_citations_and_honest_when_memory_is_empty(world):
    mem, keys, rt, reader, gw, idx = world
    c = client(world, {"answer": "The Orion launch moved to April.", "cites": [1]})
    assert c.post("/chat", json={"text": "when is the Orion launch"}).json()["answer"] == NOTHING, "nothing ingested yet: no model call, no guessing"
    assert gw.calls == [] or all(call["role"] != "planner" for call in gw.calls)
    c.post("/tick")
    r = c.post("/chat", json={"text": "when is the Orion launch"}).json()
    assert r["grounded"] and r["citations"] and "April" in r["answer"]


def test_an_answer_with_no_valid_citation_is_discarded(world):
    c = client(world, {"answer": "It is definitely June.", "cites": [99]})
    c.post("/tick")
    r = c.post("/chat", json={"text": "when is the Orion launch"}).json()
    assert r["answer"] == NOTHING and "ungrounded_answer_discarded" in r["notes"]


def test_api_still_rejects_without_token_and_bad_host(world):
    mem, keys, rt, reader, gw, idx = world
    app = create_app(TokenGuard("tok"), 8765, Services(rt, Answerer(reader, idx, gw)))
    anon = TestClient(app, base_url="http://127.0.0.1:8765")
    assert anon.get("/status").status_code == 401 and anon.get("/healthz").status_code == 200
    assert TestClient(app, base_url="http://evil.example:8765", headers={HEADER: "tok"}).get("/status").status_code == 403


def test_build_world_wires_connected_sources_and_writes_a_private_token_file(tmp_path, monkeypatch):
    import json

    from ragnarok.daemon.wiring import build_world
    from ragnarok.platform.secrets import MemoryStore, token_name

    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "home"))
    vault = MemoryStore()
    vault.set(token_name("telegram", "read"), "123:abc")
    w = build_world(vault=vault, gateway=FakeGateway(), files=[str(tmp_path)])
    try:
        assert set(w.runtime.runners) == {"telegram", "files", "notifications"}
        assert w.token_file.read_text(encoding="utf-8") == w.guard.token
        assert json.dumps(w.runtime.status())  # serialisable for the window
    finally:
        w.mem.close()


def test_the_window_page_is_served_without_a_token_but_holds_no_secret_and_loads_nothing_external(world):
    mem, keys, rt, reader, gw, idx = world
    c = TestClient(create_app(TokenGuard("S3CRET-guard-value"), 8765, Services(rt, Answerer(reader, idx, gw))), base_url="http://127.0.0.1:8765")
    r = c.get("/")
    assert r.status_code == 200 and "Ragnarok" in r.text and "S3CRET-guard-value" not in r.text
    assert "default-src 'none'" in r.text and "http://" not in r.text.split("<script>")[1] and "https://" not in r.text
    assert r.headers["x-frame-options"] == "DENY"


def test_notification_apps_are_set_through_the_api_and_start_empty(world):
    from ragnarok.connectors.notifications import Notifications, UnavailableSource

    mem, keys, rt, reader, gw, idx = world
    g = TokenGuard("tok")
    app = create_app(g, 8765, Services(rt, Answerer(reader, idx, gw), Notifications(UnavailableSource("needs Full Disk Access"), [])))
    c = TestClient(app, base_url="http://127.0.0.1:8765", headers={HEADER: "tok"})
    r = c.get("/notifications").json()
    assert r["available"] is False and "Full Disk Access" in r["detail"] and r["apps"] == []
    assert c.post("/notifications", json={"apps": ["ru.keepcoder.Telegram", " ", "ru.keepcoder.Telegram"]}).json()["apps"] == ["ru.keepcoder.Telegram"]
    assert c.get("/notifications").json()["apps"] == ["ru.keepcoder.Telegram"]


def test_messages_endpoint_lists_what_was_read_newest_first_with_search(world):
    mem, keys, rt, reader, gw, idx = world
    c = TestClient(create_app(TokenGuard("tok"), 8765, Services(rt, Answerer(reader, idx, gw), reader=reader)), base_url="http://127.0.0.1:8765", headers={HEADER: "tok"})
    c.post("/tick")
    items = c.get("/messages").json()["items"]
    assert items and items[0]["source"] == "feed" and "Orion" in items[0]["preview"]
    assert c.get("/messages?q=Orion").json()["items"] and c.get("/messages?q=zebra").json()["items"] == []


class SlowFeed(Feed):
    name = "slow"

    async def sync(self, cursor):
        await asyncio.sleep(0.4)  # a slow source, like a long poll
        return await super().sync(cursor)


class DownFeed(Feed):
    name = "down"
    calls = 0

    async def sync(self, cursor):
        DownFeed.calls += 1
        raise ConnectionError("down")


async def test_sources_are_polled_independently_a_slow_one_never_delays_a_fast_one(world):
    mem, keys, rt, reader, gw, idx = world
    q = IngestQueue(mem)
    rt.runners = {"fast": ConnectorRunner(mem, q, Feed(), "work", gateway=gw, index=idx), "slow": ConnectorRunner(mem, q, SlowFeed(), "work", gateway=gw, index=idx)}
    rt.intervals = {"fast": 0.02, "slow": 0.02}
    rt.start()
    await asyncio.sleep(0.9)
    await rt.stop()
    fast = reader._r.execute("select count(*) from items where source='feed'").fetchone()[0]
    assert fast >= 8, f"the fast source kept its own pace ({fast} cycles) while the slow one waited"
    assert rt.last["fast"]["ok"] and rt.last["slow"]["ok"]


async def test_a_failing_source_backs_off_reports_once_and_recovers_on_its_own(world, monkeypatch):
    mem, keys, rt, reader, gw, idx = world
    DownFeed.calls = 0
    rt.runners = {"down": ConnectorRunner(mem, IngestQueue(mem), DownFeed(), "work")}
    rt.intervals = {"down": 0.01}
    monkeypatch.setattr("ragnarok.daemon.runtime.asyncio.sleep", asyncio.sleep)
    rt.start()
    await asyncio.sleep(0.5)
    await rt.stop()
    assert DownFeed.calls <= 3, "exponential backoff: it does not hammer a broken service"
    assert rt.last["down"]["ok"] is False and len([a for a in rt.activity if a["kind"] == "error"]) == 1, "one entry in the feed for one continuing problem"


async def test_stopping_does_not_wait_for_a_long_poll(world):
    mem, keys, rt, reader, gw, idx = world

    class Hang(Feed):
        name = "hang"

        async def sync(self, cursor):
            await asyncio.sleep(30)

    rt.runners = {"hang": ConnectorRunner(mem, IngestQueue(mem), Hang(), "work")}
    rt.intervals = {"hang": 0.01}
    rt.start()
    await asyncio.sleep(0.2)
    t = time.monotonic()
    await rt.stop()
    assert time.monotonic() - t < 2.0


async def test_telegram_long_poll_asks_the_server_to_wait_and_returns_at_once_when_a_message_comes():
    import httpx

    from ragnarok.connectors.http import new_client
    from ragnarok.connectors.telegram import Telegram

    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 5, "message": {"message_id": 1, "date": 1, "chat": {"id": 9, "type": "private"}, "from": {"id": 9, "first_name": "A"}, "text": "hi"}}]})

    tg = Telegram("1:x", client=new_client(httpx.MockTransport(h)), long_poll_s=25)
    res = await tg.sync(None)
    assert seen["timeout"] == 25 and len(res.refs) == 1 and res.cursor.value["offset"] == 6


def test_test_scripts_can_exclude_telegram_so_they_never_consume_the_real_queue(monkeypatch):
    from ragnarok.connectors.registry import connected
    from ragnarok.platform.secrets import MemoryStore, token_name

    v = MemoryStore()
    v.set(token_name("telegram", "read"), "1:x")
    v.set(token_name("slack", "read"), "xoxb-x")
    monkeypatch.setenv("RAGNAROK_SKIP_SOURCES", "telegram")
    assert connected(v) == ["slack"]
