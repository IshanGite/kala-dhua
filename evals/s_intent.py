"""Intent reading: what is this message for? 7 classes, English, typos, other languages. Requirements: FR2.2 (analyze the intent), NFR2.1 (classification < 800 ms)."""
from __future__ import annotations

import asyncio
import time
from collections import Counter

from evals.common import REPO, lat, prop

SUITE = "intent"
REQ = "FR2.2, NFR2.1"
EXTRA = [  # written after the first 99 (scripts/eval_understand.py); typos, slang, languages
    ("wats the weather like", "chat"), ("lemme know a good pasta recipe", "chat"), ("what's 2 plus 2 lol", "chat"), ("tell me something interesting", "chat"), ("can you recommend a book", "chat"), ("good morning sunshine", "chat"),
    ("what do you think about remote work", "chat"), ("how r u", "chat"), ("thx bye", "chat"), ("define serendipity", "chat"), ("what is machine learning", "chat"), ("i feel tired today", "chat"),
    ("what did dev write about the contract", "recall"), ("did mom text", "recall"), ("anything from the bank", "recall"), ("when did i last talk to priya", "recall"), ("what was the invoice number", "recall"),
    ("find the mail about the visa appointment", "recall"), ("who invited me to the offsite", "recall"), ("what did we agree with the vendor", "recall"), ("what is the wifi password at the office", "recall"),
    ("whats new", "catchup"), ("summary pls", "catchup"), ("what did i miss on discord", "catchup"), ("give me the highlights", "catchup"), ("any urgent mail", "catchup"), ("recap my day so far", "catchup"),
    ("what's on today", "agenda"), ("anything on my calendar this week", "agenda"), ("do i have meetings tomorrow morning", "agenda"), ("what's my schedule like on friday", "agenda"), ("am i busy thursday", "agenda"),
    ("explain what's on my screen", "screen"), ("what is this window", "screen"), ("read this page to me", "screen"), ("describe what you see", "screen"),
    ("remind me to pay rent on the 1st", "action"), ("text ravi on telegram i'll be 10 min late", "action"), ("email priya@x.com the slides", "action"), ("add standup to my calendar tomorrow 10am", "action"),
    ("tell the team on slack that the release is out", "action"), ("create a doc called ideas with three bullet points", "action"), ("schedule a call with dev next monday at 4", "action"), ("draft a thank you email to meera@x.com", "action"),
    ("remind me every monday to water plants", "action"), ("post to discord general that i'm live", "action"), ("reply to mugdha saying 11 works", "action"),
    ("recuérdame llamar a mamá mañana", "action"), ("envoie un mail à sam@x.com pour dire merci", "action"), ("schick eine nachricht an ravi auf telegram: bin spät", "action"), ("resumen de mis correos de hoy", "catchup"),
    ("qué hay en mi calendario mañana", "agenda"), ("que dit ravi", "recall"), ("hallo wie geht es dir", "chat"),
    ("xqzv", "unclear"), ("ok so", "unclear"), ("the thing from before", "unclear"),
]


def dataset() -> list[tuple[str, str]]:
    src = (REPO / "scripts" / "eval_understand.py").read_text(encoding="utf-8")
    ns: dict = {}
    start = src.index("SET = [")
    end = src.index("\n]\n", start) + 3
    exec(src[start:end], ns)  # noqa: S102 - our own data literal
    return ns["SET"] + EXTRA


async def run_async() -> dict:
    from ragnarok.harness.understand import understand
    from ragnarok.llm_gateway.client import OllamaGateway
    from ragnarok.llm_gateway.models import ModelLock

    gw = OllamaGateway(ModelLock.load(REPO / "models.lock"))
    data = dataset()
    times, rows = [], []
    for text, exp in data:
        t = time.perf_counter()
        u = await understand(gw, text, None)
        times.append((time.perf_counter() - t) * 1000)
        rows.append((text, exp, u.intent if u else None))
    ok = sum(1 for _, e, g in rows if e == g)
    per = {}
    for cls in sorted({e for _, e, _ in rows}):
        sub = [(e, g) for _, e, g in rows if e == cls]
        per[cls] = prop(sum(1 for e, g in sub if e == g), len(sub))
    conf = Counter((e, g) for _, e, g in rows if e != g)
    # safety-relevant slice: a message that is NOT an action must never be read as an action, and a real action must not be missed
    non_action = [(e, g) for _, e, g in rows if e != "action"]
    action = [(e, g) for _, e, g in rows if e == "action"]
    return {"suite": SUITE, "requirement": REQ, "n": len(rows),
            "metrics": {"accuracy": prop(ok, len(rows)), "per_class": per, "false_action_rate": prop(sum(1 for _, g in non_action if g == "action"), len(non_action)),
                        "missed_action_rate": prop(sum(1 for _, g in action if g != "action"), len(action)), "latency": lat(times)},
            "confusions": [{"expected": e, "got": g, "count": c} for (e, g), c in conf.most_common(12)], "failures": [{"text": t, "expected": e, "got": g} for t, e, g in rows if e != g][:25],
            "note": "author-written labelled messages, one labeller; the class prompts were tuned on the first 99, the rest were written afterwards"}


def run() -> dict:
    return asyncio.run(run_async())
