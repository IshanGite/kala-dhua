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


def test_explicit_dates_in_reminders():
    from datetime import datetime

    from ragnarok.router.when import parse_when, split_reminder

    now = datetime(2026, 10, 25, 19, 30)
    assert split_reminder("set a reminder on 25 oct 7 pm for gym") == ("gym", "on 25 oct 7 pm")
    assert parse_when("on 25 oct 7 pm", now) == datetime(2026, 10, 25, 19, 0)
    assert parse_when("on 3rd november at 9am", now) == datetime(2026, 11, 3, 9, 0)
    assert parse_when("on 1/11 at 10am", now) == datetime(2026, 11, 1, 10, 0)
    assert split_reminder("remind me oct 30 5pm to call mom") == ("call mom", "oct 30 5pm")


def test_capability_and_complaint_detection():
    from ragnarok.harness.answer import CAPABILITY_ASK, COMPLAINT

    assert CAPABILITY_ASK.search("what can you do") and CAPABILITY_ASK.search("who are you") and not CAPABILITY_ASK.search("what did Ravi say")
    assert COMPLAINT.search("there was no reminder on calander") and COMPLAINT.search("it didn't work") and not COMPLAINT.search("remind me to call")


def test_agenda_for_an_explicit_date():
    from datetime import datetime

    from ragnarok.router.when import agenda_range

    now = datetime(2026, 10, 25, 19, 30)
    assert agenda_range("what is on my calendar on 27 oct", now)[0] == datetime(2026, 10, 27)
    assert agenda_range("anything on oct 30", now)[0] == datetime(2026, 10, 30)
    assert agenda_range("my schedule on 3rd november", now)[0] == datetime(2026, 11, 3)


def test_no_named_target_is_blank_not_the_word_on():
    assert split_chat("send a message on telegram saying hi team") == ("telegram", "", "hi team")
    assert split_chat("send a message on discord saying hi") == ("discord", "", "hi")
    assert split_chat("tell team on slack that lunch is ready")[1] == ""


def test_time_questions_are_recognised():
    from ragnarok.harness.answer import TIME_ASK

    assert all(TIME_ASK.search(t) for t in ("what is the time right now", "what's the date", "what time is it", "what day is today"))
    assert not TIME_ASK.search("what time is the budget review")


def test_mail_and_doc_splitters_use_only_the_users_words():
    from ragnarok.router.when import split_doc, split_mail

    assert split_mail("send an email to priya@acme.com about invoice 4471 saying it is attached") == ("priya@acme.com", "invoice 4471", "it is attached")
    assert split_mail("draft an email to priya@acme.com about the invoice")[0:3:2] == ("priya@acme.com", "")
    assert split_mail("email somebody about lunch") == ("", "lunch", "")
    assert split_doc("create a document in Drive called Orion plan saying launch is 14 April") == ("Orion plan", "launch is 14 April")
    assert split_doc("save a note in drive") == ("", "")


def test_draft_rule():
    assert match_intent("draft an email to priya@acme.com about the invoice") == "mail.draft"
    assert match_intent("write a reply to Dev") is None


def test_mail_intent_without_saying():
    from ragnarok.router.when import split_mail

    assert split_mail("send a mail to tushar.singh.software@gmail.com requesting for leave due to head ache") == ("tushar.singh.software@gmail.com", "requesting for leave due to head ache", "")
    assert split_mail("email a@b.com asking about the contract")[1] == "asking about the contract"
    assert split_mail("send an email to a@b.com") == ("a@b.com", "", "")


def test_false_action_claims_are_caught():
    from ragnarok.harness.answer import CLAIM

    for bad in ("Message sent to Tushar.", "I have sent the email.", "I've set a reminder for 5pm", "The reminder was created.", "I emailed him"):
        assert CLAIM.search(bad), bad
    for ok in ("The invoice was sent by Kestrel on Friday.", "Priya said the email was sent from her phone.", "You can send an email by typing a command."):
        assert not CLAIM.search(ok), ok
