"""Exact answers by code (time anywhere, date arithmetic, units, honest offline replies) checked against independent Python ground truth. Requirement: NFR2.1 (instant, exact), user trust."""
from __future__ import annotations

import random
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from evals.common import lat, prop

SUITE = "utility"
REQ = "NFR2.1 (instant answers), correctness"


def run() -> dict:
    from ragnarok.harness.utility import utility_answer

    rng = random.Random(7)
    now = datetime(2026, 10, 26, 10, 0)
    cases: list[tuple[str, callable]] = []
    cities = {"berlin": "Europe/Berlin", "tokyo": "Asia/Tokyo", "new york": "America/New_York", "london": "Europe/London", "sydney": "Australia/Sydney", "dubai": "Asia/Dubai", "paris": "Europe/Paris", "singapore": "Asia/Singapore",
              "los angeles": "America/Los_Angeles", "chicago": "America/Chicago", "moscow": "Europe/Moscow", "cairo": "Africa/Cairo", "toronto": "America/Toronto", "mumbai": "Asia/Kolkata", "seoul": "Asia/Seoul",
              "madrid": "Europe/Madrid", "rome": "Europe/Rome", "hong kong": "Asia/Hong_Kong", "sao paulo": "America/Sao_Paulo", "johannesburg": "Africa/Johannesburg"}
    here = now.astimezone()
    for city, z in cities.items():
        for form in ("what is the time in {}", "{} time"):
            exp = here.astimezone(ZoneInfo(z)).strftime("%H:%M")
            cases.append((form.format(city), lambda a, exp=exp, city=city: a is not None and f"It is {exp} " in a and city.title() in a))
    for _ in range(25):
        n = rng.randint(1, 400)
        tgt = (now + timedelta(days=n)).date()
        cases.append((f"what is the date in {n} days", lambda a, tgt=tgt: a is not None and tgt.strftime("%d %B %Y") in a))
    for d, mo in [(15, "november"), (25, "december"), (1, "january"), (14, "february"), (31, "october")]:
        cases.append((f"how many days until {d} {mo}", None))
    units = [("convert 5 km to miles", 5 / 1.609344), ("convert 10 miles to km", 10 * 1.609344), ("convert 100 f to c", (100 - 32) * 5 / 9), ("convert 30 c to f", 30 * 9 / 5 + 32), ("convert 2 kg to lb", 2000 / 453.59237),
             ("convert 150 lb to kg", 150 * 453.59237 / 1000), ("convert 3 l to gal", 3 / 3.785411784), ("convert 12 in to cm", 12 * 2.54), ("convert 6 ft to m", 6 * 0.3048), ("convert 1 gb to mb", 1024.0)]
    for q, exp in units:
        def chk(a, exp=exp):
            if a is None:
                return False
            nums = [float(x.replace(",", "")) for x in re.findall(r"=\s*(-?[\d,]+\.?\d*)", a)]
            return bool(nums) and abs(nums[0] - exp) <= max(0.01, abs(exp) * 0.001)
        cases.append((q, chk))
    for q in ("what's the weather in paris", "latest news", "bitcoin price today", "who won the match yesterday", "live score of the game", "traffic to the airport", "flight status for AI101"):
        cases.append((q, lambda a: a is not None and "offline" in a))
    for q in ("tell me a joke", "what did Ravi say", "hi", "remind me at 9 in berlin time", "send an email to a@b.com", "what is the capital of france"):
        cases.append((q, lambda a: a is None))  # not a utility question: left to the other paths
    ok, bad, times = 0, [], []
    for q, chk in cases:
        t = time.perf_counter()
        a = utility_answer(q, now)
        times.append((time.perf_counter() - t) * 1000)
        if chk is None:
            good = a is not None and "day(s) until" in a
        else:
            good = bool(chk(a))
        ok += good
        if not good:
            bad.append({"q": q, "got": a})
    return {"suite": SUITE, "requirement": REQ, "n": len(cases), "metrics": {"exact_correct": prop(ok, len(cases)), "latency": lat(times)}, "failures": bad[:12],
            "note": "ground truth from zoneinfo, datetime arithmetic and published conversion factors; a model is never involved"}
