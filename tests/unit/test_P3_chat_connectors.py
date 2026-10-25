"""P3: Slack, Telegram, Discord against recorded API responses (httpx MockTransport). No network."""

from __future__ import annotations

import json

import httpx
import pytest

from ragnarok.connectors.discord import Discord, snowflake_at
from ragnarok.connectors.http import ConnectorError, new_client
from ragnarok.connectors.runner import ConnectorRunner
from ragnarok.connectors.slack import Slack, clean_text
from ragnarok.connectors.telegram import Telegram
from ragnarok.core.clock import RealClock, SimClock, set_clock
from ragnarok.memory.queue import IngestQueue
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.service import MemoryService

NOW = 1_760_000_000_000


@pytest.fixture
def world(tmp_path, keys):
    set_clock(SimClock(NOW))
    mem = MemoryService(tmp_path / "db" / "d.db", keys)
    yield mem, keys
    mem.close()
    set_clock(RealClock())


def client(handler):
    return new_client(httpx.MockTransport(handler))


def form(req: httpx.Request) -> dict:
    return dict(httpx.QueryParams(req.content.decode()))


def slack_handler(calls):
    def h(req: httpx.Request) -> httpx.Response:
        method = req.url.path.rsplit("/", 1)[1]
        body = form(req)
        calls.append((method, body))
        if method == "users.conversations":
            return httpx.Response(200, json={"ok": True, "channels": [{"id": "C1", "name": "launch"}, {"id": "D1", "is_im": True}]})
        if method == "conversations.history":
            if body["channel"] == "C1":
                return httpx.Response(200, json={"ok": True, "has_more": False, "messages": [
                    {"ts": "1759999000.000100", "user": "U1", "text": "Saturn launch moved to <#C1|launch> Monday, ping <@U2>"},
                    {"ts": "1759999100.000200", "user": "U2", "bot_id": "B1", "text": "bot noise"},
                    {"ts": "1759999200.000300", "user": "U1", "subtype": "channel_join", "text": "joined"}]})
            return httpx.Response(200, json={"ok": True, "has_more": False, "messages": [{"ts": "1759999300.000400", "user": "U2", "text": "private note about budget"}]})
        if method == "users.info":
            return httpx.Response(200, json={"ok": True, "user": {"profile": {"display_name": "Asha"}}})
        return httpx.Response(200, json={"ok": False, "error": "unknown_method"})
    return h


def test_slack_text_cleaning():
    assert clean_text("hi <@U123ABC> see <#C1|ops> &amp; <https://x.io|the site> <!here>") == "hi @U123ABC see #ops & the site @here"


async def test_slack_end_to_end_skips_bots_and_joins_and_keeps_visibility(world):
    mem, keys = world
    calls: list = []
    sl = Slack("xoxb-test", client=client(slack_handler(calls)))
    run = ConnectorRunner(mem, IngestQueue(mem), sl, "work", require_pairing=False)
    rep = await run.cycle()
    assert rep.ingested == 2 and rep.failed == 0
    reader = MemoryReader(mem.db_path, keys)
    cid = reader.compartment_id("work")
    assert reader.fts_chunks("Saturn launch Monday", cid) and reader.fts_chunks("budget", cid)
    assert reader.fts_chunks("noise", cid) == [] and reader.fts_chunks("joined", cid) == []
    assert reader._r.execute("SELECT visibility FROM items WHERE title='#D1'").fetchone()[0] == "dm"
    assert all(b.get("token") is None for _, b in calls), "the token travels in a header, never in the body or URL"
    again = await run.cycle()
    assert again.queued == 0, "cursor advanced per channel"


async def test_slack_bad_token_is_a_clean_auth_error(world):
    sl = Slack("bad", client=client(lambda r: httpx.Response(200, json={"ok": False, "error": "invalid_auth"})))
    h = await sl.health()
    assert not h.ok and h.detail == "E_AUTH"
    with pytest.raises(ConnectorError) as e:
        await sl.sync(None)
    assert e.value.code == "E_AUTH"


async def test_slack_retries_on_429_then_succeeds():
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(429, headers={"retry-after": "0"}) if n["c"] == 1 else httpx.Response(200, json={"ok": True, "team_id": "T123"})

    assert (await Slack("x", client=client(h)).health()).ok and n["c"] == 2


def tg_handler(batches):
    def h(req):
        body = json.loads(req.content)
        if req.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [u for u in batches if u["update_id"] >= body["offset"]]})
        if req.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "ragbot"}})
        if req.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
        return httpx.Response(404, json={"ok": False, "error_code": 404})
    return h


UPDATES = [
    {"update_id": 10, "message": {"message_id": 1, "date": 1759999000, "chat": {"id": 55, "type": "private"}, "from": {"id": 55, "first_name": "Ravi"}, "text": "remind me the harbour permit expires in June"}},
    {"update_id": 11, "message": {"message_id": 2, "date": 1759999001, "chat": {"id": 66, "type": "private"}, "from": {"id": 66, "first_name": "Stranger"}, "text": "buy my crypto zebra"}},
    {"update_id": 12, "message": {"message_id": 3, "date": 1759999002, "chat": {"id": 55, "type": "private"}, "from": {"id": 7, "is_bot": True}, "text": "bot"}},
]


async def test_telegram_pairing_payload_survives_crash_and_cursor_advances(world):
    mem, keys = world
    tg = Telegram("123:abc", client=client(tg_handler(UPDATES)))
    q = IngestQueue(mem)
    run = ConnectorRunner(mem, q, tg, "work", require_pairing=True)
    run.approve_sender("55")
    await run.pull()  # crash before drain
    q.recover()
    run2 = ConnectorRunner(mem, IngestQueue(mem), Telegram("123:abc", client=client(tg_handler(UPDATES))), "work", require_pairing=True)
    rep = await run2.drain()
    assert rep.ingested == 1, "the message came from the persisted payload, not a re-fetch"
    reader = MemoryReader(mem.db_path, keys)
    cid = reader.compartment_id("work")
    assert reader.fts_chunks("harbour permit", cid) and reader.fts_chunks("zebra", cid) == []
    assert run2.load_cursor().value["offset"] == 13
    assert run2.approve_sender("66") == 1
    assert (await run2.drain()).ingested == 1


async def test_telegram_write_uses_the_separate_write_token():
    tg = Telegram("readtok", client=client(tg_handler([])))
    assert await tg.post("writetok", "55", "hello") == "77"
    assert (await tg.health()).detail == "@ragbot"


def discord_handler():
    def h(req):
        assert req.headers["authorization"].startswith("Bot ")
        p = req.url.path
        if p.endswith("/users/@me/guilds"):
            return httpx.Response(200, json=[{"id": "G1", "name": "Crew"}])
        if p.endswith("/guilds/G1/channels"):
            return httpx.Response(200, json=[{"id": "100", "type": 0, "name": "general"}, {"id": "101", "type": 2, "name": "voice"}, {"id": "102", "type": 0, "name": "secret"}])
        if p.endswith("/channels/100/messages"):
            after = int(req.url.params["after"])
            msgs = [{"id": str(after + 5), "content": "Standup moved to ten", "author": {"id": "9", "username": "meera"}, "timestamp": "2026-10-25T10:00:00Z"},
                    {"id": str(after + 6), "content": "beep", "author": {"id": "8", "username": "bot", "bot": True}}]
            return httpx.Response(200, json=msgs)
        if p.endswith("/channels/102/messages"):
            return httpx.Response(403, json={"message": "Missing Access"})
        return httpx.Response(404)
    return h


async def test_discord_reads_text_channels_skips_bots_and_forbidden_channels(world):
    mem, keys = world
    dc = Discord("tok", client=client(discord_handler()))
    run = ConnectorRunner(mem, IngestQueue(mem), dc, "work")
    rep = await run.cycle()
    assert rep.ingested == 1 and rep.failed == 0
    reader = MemoryReader(mem.db_path, keys)
    assert reader.fts_chunks("Standup moved", reader.compartment_id("work")) and reader.fts_chunks("beep", reader.compartment_id("work")) == []
    assert snowflake_at(NOW) > "0"
