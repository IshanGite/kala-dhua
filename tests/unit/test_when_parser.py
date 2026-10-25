from datetime import datetime

import pytest

from ragnarok.router.when import parse_when

NOW = datetime(2026, 10, 25, 16, 30)  # a Sunday


@pytest.mark.parametrize("text,expected", [
    ("tomorrow at 5pm", datetime(2026, 10, 26, 17, 0)),
    ("remind me tomorrow at 9:15 am to call", datetime(2026, 10, 26, 9, 15)),
    ("today at 6pm", datetime(2026, 10, 25, 18, 0)),
    ("in 2 hours", datetime(2026, 10, 25, 18, 30)),
    ("in 30 minutes", datetime(2026, 10, 25, 17, 0)),
    ("on friday", datetime(2026, 10, 30, 9, 0)),
    ("sunday at 8am", datetime(2026, 11, 1, 8, 0)),
    ("at 5pm", datetime(2026, 10, 25, 17, 0)),
    ("at 9am", datetime(2026, 10, 26, 9, 0)),
    ("tonight", datetime(2026, 10, 25, 20, 0)),
    ("tomorrow morning", datetime(2026, 10, 26, 9, 0)),
    ("2026-10-27T14:00", datetime(2026, 10, 27, 14, 0)),
    ("tomorrow at 17:45", datetime(2026, 10, 26, 17, 45)),
])
def test_parse_when(text, expected):
    assert parse_when(text, NOW) == expected


@pytest.mark.parametrize("text", ["", "call the bank", "sometime soon", "whenever"])
def test_unparseable_is_none_not_a_guess(text):
    assert parse_when(text, NOW) is None


@pytest.mark.parametrize("req,text,when", [
    ("remind me to call the bank tomorrow at 5pm", "call the bank", "tomorrow at 5pm"),
    ("remind me to buy milk in 2 hours", "buy milk", "in 2 hours"),
    ("Remind me about the dentist on friday morning", "the dentist", "on friday morning"),
    ("set a reminder to send the report today at 6pm", "send the report", "today at 6pm"),
    ("remind me to water the plants", "water the plants", ""),
])
def test_split_reminder(req, text, when):
    from ragnarok.router.when import split_reminder

    got = split_reminder(req)
    assert got == (text, when) or (got[0] == text and got[1].replace(" ", "") == when.replace(" ", ""))
