"""Everyday questions answered by CODE, exactly: the time anywhere in the world, date arithmetic, unit conversion, and an honest 'I am offline' for live facts.
A language model is never asked for a time, a date, a day of the week or a conversion factor: it guesses. These come from the system clock, the timezone database and arithmetic."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, available_timezones

from ragnarok.router.when import parse_when

ALIASES = {
    "uk": "Europe/London", "england": "Europe/London", "britain": "Europe/London", "scotland": "Europe/London", "india": "Asia/Kolkata", "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata", "pune": "Asia/Kolkata", "chennai": "Asia/Kolkata", "hyderabad": "Asia/Kolkata", "ist": "Asia/Kolkata", "china": "Asia/Shanghai", "beijing": "Asia/Shanghai", "japan": "Asia/Tokyo",
    "germany": "Europe/Berlin", "france": "Europe/Paris", "spain": "Europe/Madrid", "italy": "Europe/Rome", "netherlands": "Europe/Amsterdam", "switzerland": "Europe/Zurich", "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo", "poland": "Europe/Warsaw", "portugal": "Europe/Lisbon", "greece": "Europe/Athens", "ireland": "Europe/Dublin", "russia": "Europe/Moscow", "turkey": "Europe/Istanbul",
    "usa": "America/New_York", "us": "America/New_York", "america": "America/New_York", "nyc": "America/New_York", "new york": "America/New_York", "washington": "America/New_York", "boston": "America/New_York",
    "miami": "America/New_York", "est": "America/New_York", "edt": "America/New_York", "chicago": "America/Chicago", "texas": "America/Chicago", "cst": "America/Chicago", "denver": "America/Denver", "mst": "America/Denver",
    "california": "America/Los_Angeles", "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles", "seattle": "America/Los_Angeles", "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "canada": "America/Toronto", "mexico": "America/Mexico_City", "brazil": "America/Sao_Paulo", "argentina": "America/Argentina/Buenos_Aires", "australia": "Australia/Sydney", "new zealand": "Pacific/Auckland",
    "uae": "Asia/Dubai", "korea": "Asia/Seoul", "south korea": "Asia/Seoul", "pakistan": "Asia/Karachi", "bangladesh": "Asia/Dhaka", "nepal": "Asia/Kathmandu", "sri lanka": "Asia/Colombo", "thailand": "Asia/Bangkok",
    "vietnam": "Asia/Ho_Chi_Minh", "indonesia": "Asia/Jakarta", "malaysia": "Asia/Kuala_Lumpur", "philippines": "Asia/Manila", "egypt": "Africa/Cairo", "south africa": "Africa/Johannesburg", "nigeria": "Africa/Lagos",
    "kenya": "Africa/Nairobi", "saudi arabia": "Asia/Riyadh", "israel": "Asia/Jerusalem", "utc": "UTC", "gmt": "UTC", "cet": "Europe/Paris", "cest": "Europe/Paris", "bst": "Europe/London", "jst": "Asia/Tokyo",
    "hong kong": "Asia/Hong_Kong", "kolkata": "Asia/Kolkata", "calcutta": "Asia/Kolkata", "bombay": "Asia/Kolkata", "saigon": "Asia/Ho_Chi_Minh",
}


def _zone_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for z in available_timezones():
        if "/" in z and not z.startswith(("Etc/", "SystemV/", "US/", "Brazil/", "Canada/", "Chile/", "Mexico/", "Indian/", "Antarctica/", "Arctic/")):
            names.setdefault(z.rsplit("/", 1)[1].replace("_", " ").lower(), z)
    names.update(ALIASES)
    for k in HOLIDAYS:
        names.pop(k, None)  # 'christmas' is a holiday here, not Christmas Island
    return names


_NAMES: dict[str, str] | None = None


def find_place(text: str) -> list[tuple[str, str]]:
    """[(spoken name, IANA zone)] for the places named in the text, longest names first, in the order they were written."""
    global _NAMES
    if _NAMES is None:
        _NAMES = _zone_names()
    low = text.lower()
    hits: list[tuple[int, str, str]] = []
    taken: list[tuple[int, int]] = []
    for name in sorted(_NAMES, key=len, reverse=True):
        for m in re.finditer(rf"(?<![a-z]){re.escape(name)}(?![a-z])", low):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), name, _NAMES[name]))
    return [(n, z) for _, n, z in sorted(hits)]


def _nice(name: str) -> str:
    return name.upper() if len(name) <= 4 and name in ("ist", "utc", "gmt", "cet", "est", "pst", "uk", "usa", "uae", "us", "nyc", "sf", "bst", "jst", "edt", "pdt", "cst", "mst", "cest") else name.title()


def _diff(a: datetime, b: datetime) -> str:
    d = (a.utcoffset() or timedelta()) - (b.utcoffset() or timedelta())
    mins = int(d.total_seconds() // 60)
    if mins == 0:
        return "the same time as yours"
    h, m = divmod(abs(mins), 60)
    span = f"{h} h" + (f" {m} min" if m else "")
    return f"{span} {'ahead of' if mins > 0 else 'behind'} you"


def _clock(low: str) -> tuple[int, int] | None:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b(\d{1,2}):(\d{2})\b|\bnoon\b|\bmidnight\b", low)
    if not m:
        return None
    if m.group(0) == "noon":
        return 12, 0
    if m.group(0) == "midnight":
        return 0, 0
    if m.group(3):
        h, mm = int(m.group(1)) % 12, int(m.group(2) or 0)
        return (h + 12 if m.group(3) == "pm" else h), mm
    return int(m.group(4)), int(m.group(5))


def world_time(text: str, now_local: datetime) -> str | None:
    """'time in berlin', 'berlin time', 'what time is it in tokyo', '3pm in london in india time'. None when no place is named or it is not about the time."""
    low = text.lower()
    if not re.search(r"\b(time|clock|o'clock|date|day)\b", low) or re.match(r"^\s*(remind|schedule|set|add|put|book|block)\b", low):
        return None
    places = find_place(low)
    if not places:
        return None
    here = now_local.astimezone() if now_local.tzinfo is None else now_local
    at = _clock(low)
    if at and len(places) >= 2:  # 'what is 3pm in london in india' / 'convert 9am IST to berlin time'
        (n1, z1), (n2, z2) = places[0], places[1]
        src = datetime(here.year, here.month, here.day, at[0], at[1], tzinfo=ZoneInfo(z1))
        dst = src.astimezone(ZoneInfo(z2))
        return f"{src.strftime('%H:%M')} in {_nice(n1)} is {dst.strftime('%H:%M')} in {_nice(n2)} ({dst.strftime('%a %d %b')}, {dst.tzname()})."
    n, z = places[0]
    there = here.astimezone(ZoneInfo(z))
    return f"It is {there.strftime('%H:%M')} on {there.strftime('%A %d %B %Y')} in {_nice(n)} ({there.tzname()}). That is {_diff(there, here)}."


# ---------------------------------------------------------------- dates
HOLIDAYS = {"christmas": (12, 25), "new year": (1, 1), "new years": (1, 1), "valentine": (2, 14), "valentines": (2, 14), "halloween": (10, 31), "republic day": (1, 26), "independence day": (8, 15), "new year's eve": (12, 31)}


def _target_date(low: str, now: datetime) -> datetime | None:
    for k, (mo, d) in HOLIDAYS.items():
        if re.search(rf"\b{k}\b", low):
            yr = re.search(r"\b(20\d\d)\b", low)
            y = int(yr.group(1)) if yr else now.year + (1 if (mo, d) < (now.month, now.day) else 0)
            return datetime(y, mo, d)
    t = parse_when(re.sub(r"\b(how many days|days|until|till|to|since|from|what|which|day|date|is|on|the|of|in|will|be|be on)\b", " ", low), now)
    return t.replace(hour=0, minute=0, second=0, microsecond=0) if t else None


def date_math(text: str, now: datetime) -> str | None:
    """'how many days until 25 december', 'what day is 15 november', 'what is the date next friday / in 10 days'. None otherwise."""
    low = text.lower().strip(" ?.!")
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.search(r"how many (days|weeks)\s+(?:are there\s+)?(until|till|to|left (?:for|until|till)|since|from)\s+(.+)$", low)
    if m:
        tgt = _target_date(m.group(3), now)
        if tgt is None:
            return None
        if m.group(2) in ("until", "till", "to") and tgt < today and not re.search(r"\b(19|20)\d\d\b", m.group(3)):  # 'until 1 january' in October means next January
            try:
                tgt = tgt.replace(year=tgt.year + 1)
            except ValueError:
                tgt = tgt + timedelta(days=365)
        diff = (tgt - today).days
        if m.group(2) == "since" or m.group(2) == "from" and diff < 0:
            diff = -diff if diff < 0 else diff
            return f"{diff} day(s) since {tgt.strftime('%A %d %B %Y')}."
        unit = f"{diff} day(s)" if m.group(1) == "days" else f"{diff / 7:.1f} week(s) ({diff} days)"
        return f"{unit} until {tgt.strftime('%A %d %B %Y')}." if diff >= 0 else f"{tgt.strftime('%A %d %B %Y')} was {-diff} day(s) ago."
    m = re.search(r"\bwhat day (?:of the week )?(?:is|was|will)\s+(?:it\s+)?(?:be\s+)?(?:on\s+)?(.+)$", low) or re.search(r"\bwhich day (?:is|was)\s+(.+)$", low)
    if m and not re.fullmatch(r"(it|today|now)", m.group(1).strip()):
        tgt = _target_date(m.group(1), now)
        if tgt is not None:
            return f"{tgt.strftime('%d %B %Y')} is a {tgt.strftime('%A')}."
    m = re.search(r"(?:what(?:'s| is) the date|what date is it|date)\s+(?:will it be\s+)?(.+)$", low)
    if m and re.search(r"\b(tomorrow|yesterday|next|in \d+|after \d+|\d+ (?:days?|weeks?) (?:from now|later|ago))\b", m.group(1)):
        phrase = m.group(1)
        n = re.search(r"(\d+)\s*(day|week)s?\s*(?:from now|later)?", phrase)
        if n and not re.search(r"\bago\b", phrase):
            tgt = today + timedelta(days=int(n.group(1)) * (7 if n.group(2) == "week" else 1))
        elif n:
            tgt = today - timedelta(days=int(n.group(1)) * (7 if n.group(2) == "week" else 1))
        elif "yesterday" in phrase:
            tgt = today - timedelta(days=1)
        else:
            t = parse_when(phrase, now)
            tgt = t.replace(hour=0, minute=0) if t else None
        if tgt is not None:
            return f"{tgt.strftime('%A %d %B %Y')}."
    return None


# ---------------------------------------------------------------- units
_LEN = {"mm": 0.001, "millimeter": 0.001, "millimeters": 0.001, "cm": 0.01, "centimeter": 0.01, "centimeters": 0.01, "m": 1.0, "meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0, "km": 1000.0,
        "kilometer": 1000.0, "kilometers": 1000.0, "kilometre": 1000.0, "kilometres": 1000.0, "in": 0.0254, "inch": 0.0254, "inches": 0.0254, "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
        "yd": 0.9144, "yard": 0.9144, "yards": 0.9144, "mi": 1609.344, "mile": 1609.344, "miles": 1609.344}
_MASS = {"g": 1.0, "gram": 1.0, "grams": 1.0, "kg": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0, "kgs": 1000.0, "lb": 453.59237, "lbs": 453.59237, "pound": 453.59237, "pounds": 453.59237, "oz": 28.349523, "ounce": 28.349523, "ounces": 28.349523}
_VOL = {"ml": 0.001, "milliliter": 0.001, "milliliters": 0.001, "l": 1.0, "liter": 1.0, "liters": 1.0, "litre": 1.0, "litres": 1.0, "gal": 3.785411784, "gallon": 3.785411784, "gallons": 3.785411784, "cup": 0.2365882365, "cups": 0.2365882365}
_SPEED = {"kmh": 1.0, "km/h": 1.0, "kph": 1.0, "mph": 1.609344, "m/s": 3.6, "knots": 1.852, "knot": 1.852}
_DATA = {"b": 1.0, "kb": 1024.0, "mb": 1024.0**2, "gb": 1024.0**3, "tb": 1024.0**4}
_TEMP = {"c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}
_TABLES = [("length", _LEN), ("mass", _MASS), ("volume", _VOL), ("speed", _SPEED), ("data", _DATA)]
CURRENCY = {"usd", "dollar", "dollars", "inr", "rupee", "rupees", "eur", "euro", "euros", "gbp", "pound sterling", "jpy", "yen", "aud", "cad", "sgd", "aed", "dirham", "dirhams", "cny", "yuan", "rmb"}
_CONV = re.compile(r"(?:convert\s+)?(-?\d[\d,]*\.?\d*)\s*°?\s*([a-z/]+)\s+(?:to|in|into|=)\s+°?\s*([a-z/]+)\b|how many\s+([a-z/]+)\s+(?:are there\s+)?in\s+(-?\d[\d,]*\.?\d*)\s*°?\s*([a-z/]+)", re.I)


def _fmt(x: float) -> str:
    return f"{x:,.4f}".rstrip("0").rstrip(".") if abs(x) < 1000 else f"{x:,.2f}".rstrip("0").rstrip(".")


def convert_units(text: str) -> str | None:
    t = text.lower().strip(" ?.!")
    m = _CONV.search(t)
    if not m:
        return None
    if m.group(1):
        val, a, b = float(m.group(1).replace(",", "")), m.group(2), m.group(3)
    else:
        b, val, a = m.group(4), float(m.group(5).replace(",", "")), m.group(6)
    if a in _TEMP and b in _TEMP:
        c = val if _TEMP[a] == "c" else (val - 32) * 5 / 9 if _TEMP[a] == "f" else val - 273.15
        out = c if _TEMP[b] == "c" else c * 9 / 5 + 32 if _TEMP[b] == "f" else c + 273.15
        return f"{_fmt(val)} °{_TEMP[a].upper()} = {_fmt(out)} °{_TEMP[b].upper()}."
    for _, tab in _TABLES:
        if a in tab and b in tab:
            return f"{_fmt(val)} {a} = {_fmt(val * tab[a] / tab[b])} {b}."
    if a in CURRENCY and b in CURRENCY:
        return "I work fully offline, so I do not have today's exchange rate. Tell me a rate (for example 'convert 100 usd to inr at 83') and I will calculate it."
    return None


LIVE = re.compile(r"\b(weather|forecast|temperature outside|rain today|news|headlines|score of|live score|stock price|share price|bitcoin price|crypto price|exchange rate|traffic|flight status|who won)\b", re.I)


def live_fact(text: str) -> str | None:
    if LIVE.search(text) and not re.search(r"\b(emails?|mails?|messages?|inbox|calendar|docs?|documents?|slack|discord|telegram|drive|files?)\b", text, re.I):
        return "I run fully offline on your computer, so I cannot look up live information like weather, news, scores or prices. I can answer from your own messages, calendar and files, and I can do time, date and unit calculations."
    return None


def utility_answer(text: str, now: datetime) -> str | None:
    """The first of: time somewhere, date arithmetic, unit conversion, honest-offline. None when the text is not one of these."""
    return date_math(text, now) or world_time(text, now) or convert_units(text) or live_fact(text)
