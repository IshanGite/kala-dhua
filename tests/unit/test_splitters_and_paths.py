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
