"""The language model decides what a message is for; code decides what that may touch. Plus: stopping a job, sentence-level claim stripping, person recall."""

from __future__ import annotations

import asyncio

import pytest

from ragnarok.harness.answer import NO_CLAIM, drop_false_claims
from ragnarok.harness.understand import FOLLOWUP, understand
from ragnarok.llm_gateway.fake import FakeGateway


def gw(intent: str, **kw):
    def responder(messages, schema):
        if schema and "intent" in schema.get("properties", {}):
            return {"intent": intent, **kw}
        return "Hello! Nice to meet you."
    return FakeGateway(responder)


@pytest.mark.asyncio
async def test_understand_returns_intent_and_cleans_fields():
    u = await understand(gw("recall", person="Ravi", source="slack", topic="x" * 200), "what did Ravi say")
    assert (u.intent, u.person, u.source) == ("recall", "Ravi", "slack") and len(u.topic) <= 60


@pytest.mark.asyncio
async def test_unknown_source_and_bad_intent_are_dropped():
    u = await understand(gw("catchup", source="facebook"), "catch me up")
    assert u.source == ""
    assert await understand(gw("delete_everything"), "x") is None  # an invented intent is refused, the caller falls back


@pytest.mark.asyncio
async def test_model_down_means_none_not_a_crash():
    assert await understand(FakeGateway(lambda m, s: {}), "hi") is None


def test_only_followups_use_the_previous_message():
    assert FOLLOWUP.match("and Ravi?") and FOLLOWUP.match("what about it")
    assert not FOLLOWUP.match("what is the capital of France")


def test_false_claims_are_stripped_by_sentence():
    assert drop_false_claims("Sure, glad to help. I have sent the email. Anything else?") == "Sure, glad to help. Anything else?"
    assert drop_false_claims("I have sent it.") == NO_CLAIM
    assert drop_false_claims("Paris is the capital of France.") == "Paris is the capital of France."


@pytest.mark.asyncio
async def test_cancel_stops_a_running_job():
    from ragnarok.daemon.chat import ChatService

    class SlowAgent:
        class ctx:  # noqa: N801
            compartment = "work"
        ledger = None

        async def handle(self, text, history=None):
            await asyncio.sleep(30)

    svc = ChatService(SlowAgent())  # type: ignore[arg-type]
    r = await svc.submit("do a long thing", quick_s=0.05)
    assert r["pending"]
    assert svc.cancel(r["job"])["ok"]
    await asyncio.sleep(0.1)
    d = svc.poll(r["job"])
    assert d["pending"] is False and d.get("cancelled")


@pytest.mark.asyncio
async def test_empty_message_gets_a_friendly_reply():
    from ragnarok.daemon.chat import ChatService

    svc = ChatService(object())  # type: ignore[arg-type]
    r = await svc.submit("   ")
    assert r["ok"] and "Say something" in r["text"]


def test_old_refusals_are_not_fed_back_to_the_chat_model():
    from ragnarok.harness.answer import NO_CLAIM, clean_history

    h = [("user", "hi"), ("assistant", NO_CLAIM), ("user", "how are you"), ("assistant", "Doing well, thanks!")]
    assert clean_history(h) == [("user", "hi"), ("user", "how are you"), ("assistant", "Doing well, thanks!")]
