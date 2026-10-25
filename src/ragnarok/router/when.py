"""Deterministic 'when' parsing for reminders: a 1.7B model is unreliable at date arithmetic, so code does it. Returns None rather than guessing."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_TIME = re.compile(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b|\b([01]?\d|2[0-3]):([0-5]\d)\b", re.I)
_IN = re.compile(r"\bin\s+(\d{1,3})\s*(minute|min|hour|hr|day|week)s?\b", re.I)
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_PART = {"morning": 9, "noon": 12, "afternoon": 15, "evening": 18, "tonight": 20, "night": 20}


def _clock(text: str) -> tuple[int, int] | None:
    m = _TIME.search(text)
    if not m:
        return None
    if m.group(3):
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        return h, int(m.group(2) or 0)
    return int(m.group(4)), int(m.group(5))


def parse_when(text: str, now: datetime) -> datetime | None:
    low = (text or "").lower()
    if not low.strip():
        return None
    try:  # an ISO date-time the planner already produced
        iso = datetime.fromisoformat(text.strip())
        return iso.replace(tzinfo=None)  # the wall-clock time as written: a small model cannot know the user's zone, so any offset it invents is ignored
    except ValueError:
        pass
    m = _IN.search(low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"minute": timedelta(minutes=n), "min": timedelta(minutes=n), "hour": timedelta(hours=n), "hr": timedelta(hours=n), "day": timedelta(days=n), "week": timedelta(weeks=n)}[unit]
        return (now + delta).replace(second=0, microsecond=0)
    day = None
    if "tomorrow" in low:
        day = (now + timedelta(days=1)).date()
    elif "today" in low or "tonight" in low:
        day = now.date()
    else:
        for i, name in enumerate(_DAYS):
            if re.search(rf"\b{name}\b", low):
                ahead = (i - now.weekday()) % 7 or 7
                day = (now + timedelta(days=ahead)).date()
                break
    clock = _clock(low)
    if clock is None:
        for word, hour in _PART.items():
            if word in low:
                clock = (hour, 0)
                break
    if day is None and clock is None:
        return None
    if day is None:  # only a time of day: the next time it happens
        cand = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)  # type: ignore[index]
        return cand if cand > now else cand + timedelta(days=1)
    h, mi = clock if clock else (9, 0)
    return datetime(day.year, day.month, day.day, h, mi)


_LEAD = re.compile(r"^\s*(?:please\s+)?(?:can you\s+)?(?:remind me|set (?:a )?reminder|add (?:a )?(?:to-?do|reminder)|note)\s*(?:to|about|that|for)?\s*", re.I)
_TIMEWORDS = re.compile(
    r"\b(?:tomorrow(?:\s+(?:morning|afternoon|evening|night))?|today|tonight|this\s+(?:morning|afternoon|evening)|in\s+\d{1,3}\s*(?:minutes?|mins?|hours?|hrs?|days?|weeks?)|"
    r"(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+(?:morning|afternoon|evening|night))?|(?:at\s+)?\d{1,2}(?::[0-5]\d)?\s*(?:am|pm)|at\s+(?:[01]?\d|2[0-3]):[0-5]\d)\b", re.I)


def split_reminder(request: str) -> tuple[str, str]:
    """('call the bank', 'tomorrow at 5pm') from 'remind me to call the bank tomorrow at 5pm'. The words are the user's own; nothing is invented."""
    body = _LEAD.sub("", request.strip(), count=1)
    when = " ".join(m.group(0) for m in _TIMEWORDS.finditer(body))
    text = re.sub(r"\s{2,}", " ", _TIMEWORDS.sub("", body)).strip(" ,.;:-")
    text = re.sub(r"\b(?:at|on|by|for)\s*$", "", text, flags=re.I).strip(" ,.;:-")
    return (text or body.strip()), when


_EVENT_LEAD = re.compile(r"^\s*(?:please\s+)?(?:can you\s+)?(?:put|add|schedule|book|block(?: out)?|create|set up|plan)\s+(?:in\s+)?(?:an?\s+|the\s+|my\s+)?(?:meeting\s+)?", re.I)
_CAL = re.compile(r"\b(?:on|to|in|into|onto)\s+(?:my\s+|the\s+)?(?:google\s+)?calendar\b", re.I)


def split_event(request: str) -> tuple[str, str]:
    """('hiring plan sync', 'tomorrow at 11am') from 'put the hiring plan sync on my calendar tomorrow at 11am'."""
    body = _CAL.sub("", _EVENT_LEAD.sub("", request.strip(), count=1))
    when = " ".join(m.group(0) for m in _TIMEWORDS.finditer(body))
    title = re.sub(r"\s{2,}", " ", _TIMEWORDS.sub("", body)).strip(" ,.;:-")
    title = re.sub(r"\b(?:at|on|by|for|to)\s*$", "", title, flags=re.I).strip(" ,.;:-")
    return (title or body.strip()), when


_REPLY_LEAD = re.compile(r"^\s*(?:please\s+)?(?:can you\s+)?(?:reply|respond|answer|write back)\s+(?:to\s+)?(?:the\s+)?(?:email|mail|message)?\s*(?:from\s+)?", re.I)
_SAYING = re.compile(r"\b(?:saying|and say|and tell (?:them|him|her)|that|with|:)\s+(.+)$", re.I | re.S)
_PLATFORMS = ("telegram", "discord", "slack")


def split_reply(request: str) -> tuple[str, str]:
    """('Dev', 'I will send the contract on Thursday') from \"reply to Dev's email saying I will send the contract on Thursday\". Empty body means: ask the user."""
    m = _SAYING.search(request)
    body = m.group(1).strip().rstrip(".") if m else ""
    head = request[: m.start()] if m else request
    head = _REPLY_LEAD.sub("", head, count=1)
    target = re.sub(r"['’]s\s+(?:email|mail|message).*$|\s+(?:email|mail|message)\b.*$", "", head, flags=re.I).strip(" ,.;:-\"'")
    target = re.sub(r"^(?:the|an?)\s+", "", target, flags=re.I)
    return target, (body + "." if body and body[-1] not in ".!?" else body)


def split_chat(request: str) -> tuple[str, str, str]:
    """(platform, target, text) from 'tell Ravi on telegram that I will be late' / 'post in the discord general channel that ...'; blanks when not found."""
    low = request.lower()
    platform = next((p for p in _PLATFORMS if p in low), "")
    m = _SAYING.search(request)
    text = m.group(1).strip() if m else ""
    head = request[: m.start()] if m else request
    target = ""
    t1 = re.search(r"\b(?:tell|message|text|ping|dm|reply to|respond to)\s+(.+?)(?:\s+(?:on|in|via)\s+(?:telegram|discord|slack))?\s*$", head, re.I)
    if t1:
        target = t1.group(1)
    t2 = re.search(r"\b(?:in|on|to)\s+(?:the\s+)?(?:discord|slack|telegram)?\s*([\w#-]+)\s+channel\b", head, re.I)
    if t2:
        target = t2.group(1)
    target = re.sub(r"\s+(?:on|in|via)\s+(?:telegram|discord|slack)\b.*$", "", target, flags=re.I).strip(" ,.;:-\"'#")
    return platform, target, text
