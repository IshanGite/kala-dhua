"""Deterministic query analysis: time expressions become date ranges by CODE, never by a model (FR1.1).

All answers are in the user's time zone (default Asia/Kolkata) and returned as UTC milliseconds.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Asia/Kolkata"


def zone(name: str) -> tzinfo:
    """The IANA zone, or a safe fixed offset when the OS has no tz database (Windows without the tzdata package)."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ModuleNotFoundError, KeyError):
        return timezone(timedelta(hours=5, minutes=30)) if name == "Asia/Kolkata" else UTC
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_time_range(text: str, now_ms: int, tz: str = DEFAULT_TZ) -> tuple[int, int] | None:
    """Return (start_ms, end_ms) for the first time expression found, or None."""
    tzi = zone(tz)
    now = datetime.fromtimestamp(now_ms / 1000, tzi)
    today = _day_start(now)
    t = text.lower()
    day = timedelta(days=1)

    if re.search(r"\bday before yesterday\b", t):
        return _ms(today - 2 * day), _ms(today - day)
    if re.search(r"\byesterday\b", t):
        return _ms(today - day), _ms(today)
    if re.search(r"\btoday\b|\bthis morning\b|\bthis afternoon\b|\btonight\b", t):
        return _ms(today), _ms(today + day)
    m = re.search(r"\b(\d{1,3}) days? ago\b", t)
    if m:
        d = today - int(m.group(1)) * day
        return _ms(d), _ms(d + day)
    m = re.search(r"\blast (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        target = WEEKDAYS.index(m.group(1))
        back = (today.weekday() - target) % 7 or 7
        d = today - back * day
        return _ms(d), _ms(d + day)
    m = re.search(r"\bon (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        target = WEEKDAYS.index(m.group(1))
        back = (today.weekday() - target) % 7
        d = today - back * day
        return _ms(d), _ms(d + day)
    week_start = today - timedelta(days=today.weekday())
    if re.search(r"\blast week\b", t):
        return _ms(week_start - 7 * day), _ms(week_start)
    if re.search(r"\bthis week\b", t):
        return _ms(week_start), _ms(week_start + 7 * day)
    month_start = today.replace(day=1)
    if re.search(r"\blast month\b", t):
        prev_end = month_start
        prev_start = (month_start - day).replace(day=1)
        return _ms(prev_start), _ms(prev_end)
    if re.search(r"\bthis month\b", t):
        nxt = (month_start + 32 * day).replace(day=1)
        return _ms(month_start), _ms(nxt)
    m = re.search(r"\b(?P<pre>in |during |last |since )?(?P<m>january|february|march|april|may|june|july|august|september|october|november|december)\b(?: (?P<y>\d{4}))?", t)
    if m and not (m.group("m") == "may" and not m.group("pre") and not m.group("y")):  # a bare "may" is the verb
        month = MONTHS.index(m.group("m")) + 1
        year = int(m.group("y")) if m.group("y") else (now.year if month <= now.month else now.year - 1)
        start = datetime(year, month, 1, tzinfo=tzi)
        end = datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=tzi)
        return _ms(start), _ms(end)
    if re.search(r"\blast year\b", t):
        return _ms(datetime(now.year - 1, 1, 1, tzinfo=tzi)), _ms(datetime(now.year, 1, 1, tzinfo=tzi))
    return None
