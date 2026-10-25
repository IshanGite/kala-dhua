"""Time phrases people really use, including other languages, and the guards that keep a loose sentence from becoming the wrong action."""

from __future__ import annotations

from datetime import datetime

import pytest

from ragnarok.harness.turn import GENERIC, Agent
from ragnarok.router.when import localize_when, parse_when, split_chat

NOW = datetime(2026, 10, 25, 22, 0)


@pytest.mark.parametrize("phrase,expected", [
    ("in an hour", datetime(2026, 10, 25, 23, 0)), ("in half an hour", datetime(2026, 10, 25, 22, 30)), ("in a minute", datetime(2026, 10, 25, 22, 1)),
    ("in a couple of hours", datetime(2026, 10, 26, 0, 0)), ("midnight", datetime(2026, 10, 26, 0, 0)), ("in 45 min", datetime(2026, 10, 25, 22, 45)),
])
def test_relative_phrases(phrase, expected):
    assert parse_when(phrase, NOW) == expected


@pytest.mark.parametrize("phrase,expected", [("कल सुबह 9 बजे", datetime(2026, 10, 26, 9, 0)), ("kal shaam 6 baje", datetime(2026, 10, 26, 18, 0)), ("आज रात 11 बजे", datetime(2026, 10, 25, 23, 0))])
def test_hindi_and_hinglish_times(phrase, expected):
    assert parse_when(localize_when(phrase), NOW) == expected


def test_chat_message_without_that():
    assert split_chat("tell Tushar on telegram I will be late") == ("telegram", "Tushar", "I will be late")
    assert split_chat("tell Ravi on telegram that I quit")[2] == "I quit"


def test_only_the_users_own_words_can_be_a_message_or_a_reminder():
    f = Agent._faithful
    assert f("I will be late", "tell ravi on telegram I will be late")
    assert not f("Thank you for your email, I hope this finds you well", "reply to dev saying tomorrow works")
    assert "things" in GENERIC and "stuff" in GENERIC


def test_an_app_the_user_never_named_cannot_be_chosen_by_a_guess():
    assert Agent._platform_named("telegram", "can u text tushar on tg that im omw")
    assert not Agent._platform_named("discord", "reply to dev saying thursday works")
