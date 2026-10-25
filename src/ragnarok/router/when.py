"""Deterministic 'when' parsing for reminders: a 1.7B model is unreliable at date arithmetic, so code does it. Returns None rather than guessing."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_TIME = re.compile(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b|\b([01]?\d|2[0-3]):([0-5]\d)\b", re.I)
_IN = re.compile(r"\bin\s+(\d{1,3})\s*(minute|min|hour|hr|day|week)s?\b", re.I)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MON})\b(?:\s+(\d{{4}}))?", re.I)
_MDY = re.compile(rf"\b({_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)
_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
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


def _safe_date(now: datetime, d: int, mo: int, yr: str | None):  # noqa: ANN202
    try:
        year = int(yr) + (2000 if yr and len(yr) == 2 else 0) if yr else now.year
        return datetime(year, mo, d).date()
    except ValueError:
        return None


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
    ex = _DMY.search(low)
    if ex:
        d, mo, yr = int(ex.group(1)), _MONTHS.index(ex.group(2)[:3].lower()) + 1, ex.group(3)
        day = _safe_date(now, d, mo, yr)
    if day is None and (ex := _MDY.search(low)):
        mo, d, yr = _MONTHS.index(ex.group(1)[:3].lower()) + 1, int(ex.group(2)), ex.group(3)
        day = _safe_date(now, d, mo, yr)
    if day is None and (ex := _NUM.search(low)):
        d, mo, yr = int(ex.group(1)), int(ex.group(2)), ex.group(3)  # day/month, the common order in India
        day = _safe_date(now, d, mo, yr)
    if day is not None:
        pass
    elif "tomorrow" in low:
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


_LEAD = re.compile(r"^\s*(?:please\s+)?(?:can you\s+)?(?:remind me|set (?:a )?reminder|add (?:a )?(?:to-?do|reminder)|note)\s*(?:(?:to|about|that|for)\b)?\s*", re.I)
_TIMEWORDS = re.compile(
    r"\b(?:(?:on\s+)?\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MON + r"(?:\s+\d{4})?|(?:on\s+)?" + _MON + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|(?:on\s+)?\d{1,2}/\d{1,2}(?:/\d{2,4})?|tomorrow(?:\s+(?:morning|afternoon|evening|night))?|today|tonight|this\s+(?:morning|afternoon|evening)|in\s+\d{1,3}\s*(?:minutes?|mins?|hours?|hrs?|days?|weeks?)|"
    r"(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+(?:morning|afternoon|evening|night))?|(?:at\s+)?\d{1,2}(?::[0-5]\d)?\s*(?:am|pm)|at\s+(?:[01]?\d|2[0-3]):[0-5]\d)\b", re.I)


def split_reminder(request: str) -> tuple[str, str]:
    """('call the bank', 'tomorrow at 5pm') from 'remind me to call the bank tomorrow at 5pm'. The words are the user's own; nothing is invented."""
    body = _LEAD.sub("", request.strip(), count=1)
    when = " ".join(m.group(0) for m in _TIMEWORDS.finditer(body))
    text = re.sub(r"\s{2,}", " ", _TIMEWORDS.sub("", body)).strip(" ,.;:-")
    text = re.sub(r"\b(?:at|on|by|for)\s*$", "", text, flags=re.I).strip(" ,.;:-")
    text = re.sub(r"^(?:to|about|that|for)\b\s*", "", text, flags=re.I).strip(" ,.;:-")
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
_NOT_A_TARGET = {"on", "in", "via", "a", "an", "the", "to", "message", "msg", "text", "telegram", "discord", "slack", "saying", "that", "everyone", "team", "all"}


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
    ms = re.search(r"\bsend\s+(?:a\s+)?(?:message\s+)?(?:saying\s+)?(.+?)\s+to\s+(.+?)\s+(?:on|in|via)\s+(telegram|discord|slack)\b", request, re.I)
    if ms:
        return ms.group(3).lower(), ms.group(2).strip(" ,.;:-\"'#"), ms.group(1).strip()
    ms = re.search(r"\bsend\s+(?:a\s+)?(?:message\s+)?(?:saying\s+)?(.+?)\s+to\s+(.+?)\s+(?:on|in|via)\s+(telegram|discord|slack)\b", request, re.I)
    if ms:
        return ms.group(3).lower(), ms.group(2).strip(" ,.;:-\"'#"), ms.group(1).strip()
    t1 = re.search(r"\b(?:tell|message|text|ping|dm|reply to|respond to)\s+(.+?)(?:\s+(?:on|in|via)\s+(?:telegram|discord|slack))?\s*$", head, re.I)
    if t1:
        target = t1.group(1)
    t2 = re.search(r"\b(?:in|on|to)\s+(?:the\s+)?(?:discord|slack|telegram)?\s*([\w#-]+)\s+channel\b", head, re.I)
    if t2:
        target = t2.group(1)
    target = re.sub(r"\s+(?:on|in|via)\s+(?:telegram|discord|slack)\b.*$", "", target, flags=re.I).strip(" ,.;:-\"'#")
    if re.fullmatch(r"(?:on|in|via)\s+(?:telegram|discord|slack)", target, re.I) or target.lower() in _NOT_A_TARGET or re.fullmatch(r"(?:an?\s+)?(?:new\s+)?(?:message|msg|text)(?:\s+(?:on|in|via)\s+\w+)?", target, re.I):
        target = ""  # 'send a message on telegram saying hi' names nobody: the caller picks the most recent conversation and shows it on the card
    return platform, target, text


def agenda_range(text: str, now: datetime) -> tuple[datetime, datetime]:
    """The day or span an agenda question is about, in local time: today (default), tomorrow, the day after, a weekday, this week, next week."""
    low = text.lower()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    one = timedelta(days=1)
    for rx, order in ((_DMY, "dmy"), (_MDY, "mdy"), (_NUM, "num")):  # an explicit date wins: 'on 27 oct', 'oct 27', '27/10'
        m = rx.search(low)
        if m:
            g = m.groups()
            d, mo, yr = (int(g[0]), _MONTHS.index(g[1][:3].lower()) + 1, g[2]) if order == "dmy" else (int(g[1]), _MONTHS.index(g[0][:3].lower()) + 1, g[2]) if order == "mdy" else (int(g[0]), int(g[1]), g[2])
            day = _safe_date(now, d, mo, yr)
            if day is not None:
                d0 = datetime(day.year, day.month, day.day)
                return d0, d0 + one
    if "day after tomorrow" in low:
        return start + 2 * one, start + 3 * one
    if "tomorrow" in low:
        return start + one, start + 2 * one
    if "next week" in low:
        mon = start + timedelta(days=7 - start.weekday())
        return mon, mon + timedelta(days=7)
    if "this week" in low or "the week" in low:
        mon = start - timedelta(days=start.weekday())
        return mon, mon + timedelta(days=7)
    for i, name in enumerate(_DAYS):
        if re.search(rf"\b{name}\b", low):
            d = start + timedelta(days=(i - start.weekday()) % 7)
            return d, d + one
    return start, start + one


_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def split_mail(request: str) -> tuple[str, str, str]:
    """(to, subject, body) from the user's own words: 'send an email to a@b.com about the invoice saying it is attached'. Blank where the user did not say it."""
    m = _EMAIL.search(request)
    to = m.group(0) if m else ""
    sm = _SAYING.search(request)
    body = sm.group(1).strip() if sm else ""
    head = request[: sm.start()] if sm else request
    sub = re.search(r"\b(?:about|regarding|re:|subject)\s+(.+?)\s*$", head, re.I)
    subject = sub.group(1).strip(" ,.;:-") if sub else ""
    return to, subject, body


def split_doc(request: str) -> tuple[str, str]:
    """(title, text) from 'create a document in Drive called Plan saying the key dates'."""
    t = re.search(r"\b(?:called|named|titled|title)\s+(.+?)(?:\s+(?:saying|with|that|containing|about)\b|$)", request, re.I)
    title = t.group(1).strip(" ,.;:-\"'") if t else ""
    sm = _SAYING.search(request)
    text = sm.group(1).strip() if sm else ""
    return title, text
