"""Connection questions are answered from the real state by code; apps that have no connector are never 'connected' and never sent to."""

from __future__ import annotations

import asyncio

import pytest

from ragnarok.core.clock import RealClock, set_clock
from ragnarok.daemon.wiring import build_world, warm_up
from ragnarok.harness.connections import answer_connection, mentioned, refuse_unsupported_send
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.platform.secrets import MemoryStore

STATE = {"read": ["telegram", "gmail"], "write": ["telegram"], "health": {"telegram": {"ok": True}, "gmail": {"ok": True}, "notifications": {"ok": None}}}


def test_whatsapp_is_never_connected_and_says_why():
    t = answer_connection("tell me if whatsapp is connected", STATE)
    assert "not connected" in t and "no WhatsApp connector" in t and "Notifications" in t
    assert "yes" not in t.lower().split()


@pytest.mark.parametrize("q", ["is whats app connected?", "are you connected to WhatsApp", "do you have access to my whatsapp", "can you read my whatsapp messages"])
def test_many_phrasings_for_whatsapp(q):
    assert "not connected" in answer_connection(q, STATE)


def test_connected_and_not_connected_known_apps():
    assert "Telegram: connected, reading and sending" in answer_connection("is telegram connected", STATE)
    assert "Gmail: connected, reading" in answer_connection("is gmail connected?", STATE)
    t = answer_connection("is slack connected", STATE)
    assert "Slack: not connected" in t and "ragnarok connect slack" in t


def test_generic_question_lists_both_sides_and_the_limit():
    t = answer_connection("which apps are connected", STATE)
    assert t.startswith("Connected:") and "Telegram (read and send)" in t and "Not connected:" in t and "WhatsApp" in t


def test_other_sentences_are_left_alone():
    for q in ["what did Ravi say about the demo", "email dev@example.com that the build is working", "remind me to call mom", "is the meeting at three"]:
        assert answer_connection(q, STATE) is None


def test_sending_on_an_unsupported_app_is_refused():
    assert "did not send anything" in refuse_unsupported_send("send a whatsapp message to mom saying hi")
    assert "Outlook is not used" in refuse_unsupported_send("email priya on outlook that I am late")
    assert refuse_unsupported_send("tell Ravi on telegram that the demo moved") is None
    assert refuse_unsupported_send("is whatsapp connected") is None  # a question, answered by answer_connection
    assert mentioned("use the calendar and whatsapp") == (["gcal"], ["WhatsApp"])


async def test_through_the_agent_nothing_is_sent_and_no_model_is_asked(tmp_path, monkeypatch):
    set_clock(RealClock())
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "home"))
    sent: list = []

    async def fake_post(chat_id, text):
        sent.append((chat_id, text))
        return "m1"

    calls: list = []

    def responder(m, s):
        calls.append(1)
        return "Yes, WhatsApp is connected." if s is None else {}

    w = build_world(vault=MemoryStore(), gateway=FakeGateway(responder), sandbox=False, use_laya=False, embed_rungs=False, senders={"telegram": fake_post}, max_cpu=101)
    try:
        await warm_up(w)
        for q, want in [("tell me if whatsapp is connected", "no WhatsApp connector"), ("send a whatsapp message to mom saying hi", "did not send anything"), ("which apps are connected", "Connected:")]:
            r = await asyncio.wait_for(w.services.chat.submit(q, quick_s=5), 10)
            assert want in r["text"], r["text"]
            assert "Yes, WhatsApp" not in r["text"]
        assert sent == [] and calls == []
    finally:
        w.mem.close()
