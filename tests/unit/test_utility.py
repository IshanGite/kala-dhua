"""Everyday questions answered by code: time anywhere, date arithmetic, units, honest offline answers."""

from __future__ import annotations

from datetime import datetime

import pytest

from ragnarok.harness.utility import (
    convert_units,
    date_math,
    find_place,
    live_fact,
    utility_answer,
    world_time,
)

NOW = datetime(2026, 10, 25, 19, 0)  # a Sunday, local


@pytest.mark.parametrize("q,place", [("What is the time in berlin right now", "Berlin"), ("berlin time", "Berlin"), ("time in new york", "New York"), ("what time is it in tokyo", "Tokyo"), ("time in delhi", "Delhi")])
def test_time_in_a_named_place(q, place):
    out = world_time(q, NOW)
    assert out and place in out and "ahead of you" in out or "behind you" in out or "same time" in out


def test_berlin_is_not_the_local_time():
    assert "Berlin" in world_time("What is the time in berlin right now", NOW)  # the screenshot bug: it answered with the local time


def test_clock_conversion_between_two_places():
    assert world_time("what is 3pm in london in india time", NOW) == "15:00 in London is 20:30 in India (Sun 25 Oct, IST)."


def test_no_place_means_not_a_world_time_question():
    assert world_time("what time is it", NOW) is None and world_time("remind me at 9 in berlin time", NOW) is None


def test_christmas_is_a_holiday_not_an_island():
    assert find_place("what day is christmas 2027") == []
    assert date_math("what day is christmas 2027", NOW) == "25 December 2027 is a Saturday."


@pytest.mark.parametrize("q,expected", [("how many days until christmas", "61 day(s) until Friday 25 December 2026."), ("what day is 15 november", "15 November 2026 is a Sunday."),
                                        ("what is the date in 10 days", "Wednesday 04 November 2026."), ("what's the date tomorrow", "Monday 26 October 2026.")])
def test_date_arithmetic(q, expected):
    assert date_math(q, NOW) == expected


@pytest.mark.parametrize("q,expected", [("convert 5 km to miles", "5 km = 3.1069 miles."), ("72 f to c", "72 °F = 22.2222 °C."), ("how many feet in 3 meters", "3 meters = 9.8425 feet."), ("10 gb to mb", "10 gb = 10,240 mb.")])
def test_unit_conversion(q, expected):
    assert convert_units(q) == expected


def test_currency_without_a_rate_is_honest():
    assert "exchange rate" in convert_units("100 usd to inr")


def test_live_facts_are_refused_honestly_but_your_own_mail_is_not():
    assert "offline" in live_fact("what's the weather in paris")
    assert live_fact("summarize the weather emails in my inbox") is None


def test_everything_else_is_left_to_the_other_paths():
    assert utility_answer("tell me a joke", NOW) is None and utility_answer("what did Ravi say", NOW) is None
