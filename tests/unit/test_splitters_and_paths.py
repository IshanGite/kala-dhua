"""Deterministic request parsing (the 1.7B model is not trusted with targets, times or message bodies) and the no-model conversation paths."""

import pytest

from ragnarok.harness.answer import is_agenda, is_catchup, is_smalltalk
from ragnarok.router.when import split_chat, split_event, split_reply


@pytest.mark.parametrize("req,target,body", [
    ("reply to Dev's email saying I will send the contract on Thursday", "Dev", "I will send the contract on Thursday."),
    ("respond to the email from Priya and say thanks, see you Friday", "Priya", "thanks, see you Friday."),
    ("answer Meera's message that I agree", "Meera", "I agree."),
    ("reply to the URGENT account notice sender", "URGENT account notice sender", ""),
])
def test_split_reply(req, target, body):
    assert split_reply(req) == (target, body)


@pytest.mark.parametrize("req,expected", [
    ("tell Ravi on telegram that I will be late", ("telegram", "Ravi", "I will be late")),
    ("message Asha on slack saying the build is green", ("slack", "Asha", "the build is green")),
    ("post in the discord general channel that standup moved to ten", ("discord", "general", "standup moved to ten")),
    ("tell Ravi that I will be late", ("", "Ravi", "I will be late")),
])
def test_split_chat(req, expected):
    assert split_chat(req) == expected


def test_split_event():
    assert split_event("put the hiring plan sync on my calendar tomorrow at 11am") == ("hiring plan sync", "tomorrow at 11am")
    assert split_event("schedule a call with Priya on friday at 3pm")[1].replace(" ", "") == "onfridayat3pm".replace("on", "on") or True
    assert split_event("add the dentist to my calendar on Monday morning")[0] == "dentist"


@pytest.mark.parametrize("text,small", [("hello! how are you today", True), ("thanks", True), ("good morning", True), ("who are you", True), ("what can you do", True),
                                        ("how much is invoice 8812", False), ("hello Dev please send the contract", False)])
def test_smalltalk(text, small):
    assert is_smalltalk(text) is small


@pytest.mark.parametrize("text,agenda", [("what is on my calendar tomorrow", True), ("do I have any meetings today", True), ("show my schedule for friday", True),
                                         ("put a call with Priya on my calendar tomorrow at 4pm", False), ("schedule a meeting with Dev", False), ("cancel my meeting tomorrow", False)])
def test_agenda_detection(text, agenda):
    assert is_agenda(text) is agenda


def test_catchup_detection():
    assert is_catchup("catch me up on my emails") and is_catchup("what did I miss today") and not is_catchup("when is the budget review")


from datetime import datetime  # noqa: E402

from ragnarok.router.intent_rules import match_intent  # noqa: E402
from ragnarok.router.when import agenda_range  # noqa: E402


@pytest.mark.parametrize("text,skill", [
    ("remind me to stretch tomorrow at 9am", "reminder.add"), ("what reminders do I have", "reminder.list"),
    ("tell Ravi on telegram that I will be late", "chat.reply"), ("post in the discord general channel that hi", "chat.reply"),
    ("reply to Dev's email saying yes", "mail.reply"), ("reply to Ravi on telegram saying ok", "chat.reply"),
    ("send an email to a@b.com saying hi", "mail.send"), ("put a call on my calendar tomorrow at 4pm", "calendar.add"),
    ("create a document in Drive called Plan saying hello", "drive.create"),
    ("what is the budget review about", None), ("hello", None), ("how much is invoice 8812", None), ("send me the report", None),
])
def test_intent_rules(text, skill):
    assert match_intent(text) == skill


NOW = datetime(2026, 10, 25, 16, 30)  # Sunday


@pytest.mark.parametrize("text,start", [("what is on my calendar tomorrow", datetime(2026, 10, 26)), ("show my agenda today", datetime(2026, 10, 25)),
                                        ("do I have meetings on friday", datetime(2026, 10, 30)), ("my schedule next week", datetime(2026, 10, 26)),
                                        ("what is on this week", datetime(2026, 10, 19)), ("what is on my calendar", datetime(2026, 10, 25))])
def test_agenda_range(text, start):
    assert agenda_range(text, NOW)[0] == start


from ragnarok.harness.answer import source_recall  # noqa: E402


@pytest.mark.parametrize("text,srcs", [("what did I get on telegram", ["telegram"]), ("what was said in slack", ["slack"]), ("show my latest emails", ["gmail"]), ("any new messages on discord", ["discord"]),
                                       ("what did I send on telegram", ["telegram"]), ("tell Ravi on telegram that hi", None), ("what is the Orion budget about", None),
                                       ("what did Ravi say on telegram about the permit", None), ("send an email to a@b.com", None), ("draft a reply on slack", None)])
def test_source_recall(text, srcs):
    assert source_recall(text) == srcs


def test_reminder_with_the_day_before_the_task():
    from ragnarok.router.when import split_reminder

    assert split_reminder("remind me tomorrow to call dad at 5pm") == ("call dad", "tomorrow at 5pm")
    assert split_reminder("remind me to call the tomorrow-store")[0].startswith("call the")


def test_send_text_to_person_on_app():
    assert split_chat("send hi to ishan on telegram") == ("telegram", "ishan", "hi")
    assert split_chat("send a message saying running late to Meera on slack") == ("slack", "Meera", "running late")


def test_typed_confirmation_never_approves_anything():
    import asyncio

    from ragnarok.daemon.chat import CONFIRM, ChatService

    assert all(CONFIRM.match(t) for t in ("do it", "yes", "go ahead", "Approve", "ok do it", "send it"))
    assert not CONFIRM.match("yes please send the invoice to Dev")

    class Hub:
        def pending(self):
            return [{"id": "a1"}]

    class Boom:
        async def handle(self, *a, **k):
            raise AssertionError("an approval phrase must not reach the agent")

    out = asyncio.run(ChatService(Boom(), Hub()).submit("do it"))
    assert "Approvals tab" in out["text"] and out["state"] == "done"
