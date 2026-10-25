"""The agent end to end on the scripted fake gateway: reminders by code, approval cards for sends, no memory text in outgoing messages, grounded answers, route learning with free text refilled."""

from __future__ import annotations

import asyncio
import json

import pytest

from ragnarok.core.clock import RealClock, set_clock
from ragnarok.daemon.chat import summarize
from ragnarok.daemon.wiring import build_world, warm_up
from ragnarok.ingest.pipeline import ingest_text
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.platform.secrets import MemoryStore

SENT: list = []


async def fake_post(chat_id, text):
    SENT.append(("post", chat_id, text))
    return "m1"


async def fake_cal(p, due):
    SENT.append(("calendar", p["title"], due))
    return "https://calendar.example/e1"


async def fake_gmail_draft(draft):
    SENT.append(("gmail_draft", draft["to"]))
    return "d1"


def responder(messages, schema):
    props = (schema or {}).get("properties", {})
    user = messages[-1]["content"]
    if "skill" in props and "needs_plan" in props:  # L2 arbiter: pick by keyword
        req = user.split('Request: """')[1].split('"""')[0].lower() if 'Request: """' in user else user.lower()
        pick = "reminder.add" if "remind" in req else "chat.reply" if "tell " in req else "calendar.add" if "calendar" in req else "mail.draft" if "draft" in req else "memory.search"
        return {"skill": {"choice": pick, "confidence": 0.85}, "needs_plan": {"answer": False, "confidence": 0.85}}
    if "text" in props and "platform" in props:  # chat.reply params
        return {"platform": "telegram", "target": "Ravi", "text": "I will be late"}
    if "title" in props and "when" in props:
        return {"title": "Call with Priya", "when": "tomorrow at 3pm"}
    if "to" in props and "body" in props and "subject" in props:
        return {"to": "priya@acme.com", "subject": "Invoice", "body": "Hi Priya, the invoice is attached."}
    if "query" in props:
        return {"query": "standup"}
    if "answer" in props and "cites" in props:
        return {"answer": "The standup moved to ten.", "cites": [1]}
    return {}


@pytest.fixture
def world(tmp_path, monkeypatch):
    set_clock(RealClock())
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "home"))
    SENT.clear()
    w = build_world(vault=MemoryStore(), gateway=FakeGateway(responder), sandbox=False, use_laya=False, embed_rungs=False, senders={"telegram": fake_post, "calendar": fake_cal, "gmail_draft": fake_gmail_draft}, max_cpu=101)
    yield w
    w.mem.close()


async def start(w):
    await warm_up(w)
    await ingest_text(w.mem, w.gateway, w.index, source="telegram", native_id="55:1", compartment="work", trust="received", text="Standup moved to ten tomorrow", title="Ravi")


async def approve_all(w, approve=True):
    seen = []
    while True:
        for card in w.services.hub.pending():
            seen.append(card)
            w.services.hub.decide(card["id"], approve)
        await asyncio.sleep(0.01)
        if seen:
            return seen


async def test_reminder_is_parsed_by_code_and_stored(world):
    await start(world)
    turn = await world.agent.handle("remind me to call the bank tomorrow at 5pm")
    out = summarize(turn)
    assert "Reminder set: call the bank" in out["text"] and "17:00" in out["text"]
    due = world.services.runtime.commitments.list_open()
    assert [d["what"] for d in due] == ["call the bank"] and due[0]["due_at"]


async def test_a_send_waits_for_a_click_and_uses_only_the_users_words(world):
    await start(world)
    task = asyncio.create_task(world.agent.handle("tell Ravi on telegram that I will be late"))
    cards = await asyncio.wait_for(approve_all(world, True), 5)
    out = summarize(await asyncio.wait_for(task, 5))
    assert cards[0]["skill"] == "chat.reply" and SENT == [("post", "55", "I will be late")] and "posted" in out["text"].lower()


async def test_denying_the_card_sends_nothing(world):
    await start(world)
    task = asyncio.create_task(world.agent.handle("tell Ravi on telegram that I will be late"))
    await asyncio.wait_for(approve_all(world, False), 5)
    out = summarize(await asyncio.wait_for(task, 5))
    assert SENT == [] and "posted" not in out["text"].lower()


async def test_memory_question_is_answered_with_citations(world):
    await start(world)
    out = summarize(await world.agent.handle("what did Ravi say about the standup"))
    assert out["grounded"] and out["citations"] and "ten" in out["text"]


async def test_a_repeated_request_becomes_a_stored_route(world):
    await start(world)
    tiers = []
    for _ in range(5):
        t = await world.agent.handle("remind me to buy milk in 2 hours")
        tiers.append((t.tier, t.learned))
    assert tiers[0][1] == "candidate_created" and tiers[2][1] == "promoted" and tiers[3][0] == "L0"
    assert [d["what"] for d in world.services.runtime.commitments.list_open()].count("buy milk") == 5, "every replay refilled the free text from the new request"


async def test_due_reminders_fire_once_even_when_ingestion_is_off(world):
    await start(world)
    world.services.runtime.set_enabled(False)
    c = world.services.runtime.commitments
    c.add("work", "stand up", 1)
    assert [r["what"] for r in world.services.runtime.fire_reminders()] == ["stand up"]
    assert world.services.runtime.fire_reminders() == []
    assert "stand up" in json.dumps(list(world.services.runtime.reminders))


async def test_a_calendar_event_waits_for_a_click_then_lands_in_the_calendar(world):
    await start(world)
    task = asyncio.create_task(world.agent.handle("put a call with Priya on my calendar tomorrow at 3pm"))
    cards = await asyncio.wait_for(approve_all(world, True), 5)
    out = summarize(await asyncio.wait_for(task, 5))
    assert cards[0]["skill"] == "calendar.add" and any(f["value"].lower().startswith("call with priya") or f["name"] == "title" for f in cards[0]["fields"]) and SENT[-1][0] == "calendar" and SENT[-1][1].lower() == "call with priya" and "Added to Google Calendar" in out["text"]


async def test_a_mail_draft_goes_to_gmail_drafts_not_a_send(world):
    await start(world)
    out = summarize(await world.agent.handle("draft an email to priya@acme.com about the invoice"))
    assert SENT[-1] == ("gmail_draft", "priya@acme.com") and "Gmail drafts" in out["text"]
