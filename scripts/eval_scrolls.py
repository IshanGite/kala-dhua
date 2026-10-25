"""Measured results for Skill Scrolls: routing accuracy, each scroll's own self-tests, and the token saving of progressive loading.
Run: uv run python scripts/eval_scrolls.py   (real local models; writes docs/evidence/scrolls.json). The routing set is author-written (one labeller): a regression guard, not a benchmark."""
import asyncio
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, "src")
from pathlib import Path

from ragnarok.llm_gateway.client import OllamaGateway
from ragnarok.llm_gateway.models import ModelLock
from ragnarok.scrolls.library import ScrollLibrary
from ragnarok.scrolls.runner import ScrollRunner

ROUTING = [  # (message, expected scroll or None)
    ("summarize these meeting notes: Ravi will ship Friday, Asha needs review", "meeting-notes"), ("turn my notes into action items", "action-items"), ("clean up my meeting notes", "meeting-notes"),
    ("triage my inbox", "email-triage"), ("which emails need a reply", "email-triage"), ("sort my emails by importance", "email-triage"), ("draft a reply to this email: can you send the contract", "reply-drafter"),
    ("how should I answer this message from my manager", "reply-drafter"), ("explain this like I'm five: what is encryption", "explain-simply"), ("explain this simply", "explain-simply"),
    ("what does this mean in plain english", "explain-simply"), ("list the action items", "action-items"), ("extract the tasks from this", "action-items"), ("help me decide between a lodge and a city loft", "decision-helper"),
    ("pros and cons of renting vs buying", "decision-helper"), ("write my standup", "standup-writer"), ("draft my daily update", "standup-writer"), ("triage my inbox and draft the first reply", "triage-and-reply"),
    ("use the explain-simply scroll on photosynthesis", "explain-simply"),
    ("hi", None), ("what is the capital of france", None), ("tell Ravi on telegram I am late", None), ("what did Mugdha say", None), ("remind me to call mom at 5", None), ("catch me up", None),
    ("what is the time in tokyo", None), ("thanks", None), ("tell me a joke", None), ("what's on my calendar tomorrow", None),
]

HOLDOUT = [  # written AFTER the triggers were tuned on ROUTING above: this is the honest number
    ("can you tidy up the notes from today's call with the vendor", "meeting-notes"), ("which of these mails should I deal with first", "email-triage"), ("how do I respond to this complaint from a customer", "reply-drafter"),
    ("make this contract clause understandable for a beginner", "explain-simply"), ("pull out the to-dos from this chat log", "action-items"), ("I can't choose between two laptops, help me weigh them", "decision-helper"),
    ("write up my update for the team meeting", "standup-writer"), ("go through my messages, find the most urgent and write back", "triage-and-reply"),
    ("what is the weather like", None), ("send an email to dev@example.com", None), ("what is my password", None), ("good evening", None), ("who is the prime minister of canada", None), ("what is on my screen", None),
]


async def main():
    gw = OllamaGateway(ModelLock.load(Path("models.lock")))
    lib = ScrollLibrary(Path("src/ragnarok/scrolls/builtin"), Path(tempfile.mkdtemp()), gw)
    ok, miss, t0 = 0, [], time.time()
    for msg, exp in ROUTING:
        r = await lib.route(msg)
        got = r.name if r else None
        ok += got == exp
        if got != exp:
            miss.append({"message": msg, "expected": exp, "got": got, "score": round(r.score, 3) if r else None})
    route_ms = (time.time() - t0) / len(ROUTING) * 1000
    h_ok, h_miss = 0, []
    for msg, exp in HOLDOUT:
        r = await lib.route(msg)
        got = r.name if r else None
        h_ok += got == exp
        if got != exp:
            h_miss.append({"message": msg, "expected": exp, "got": got, "score": round(r.score, 3) if r else None})
    runner = ScrollRunner(lib, gw, None, None)  # type: ignore[arg-type]
    tests = {}
    for s in lib.usable():
        if s.examples:
            p, t, detail = await runner.self_test(s.name)
            tests[s.name] = {"passed": p, "total": t, "failures": [d for d in detail if not d["ok"]]}
    head = sum(s.approx_tokens()[0] for s in lib.usable())
    full = sum(s.approx_tokens()[1] for s in lib.usable())
    one = sum(s.approx_tokens()[1] for s in lib.usable())  # everything in one prompt
    out = {"n_scrolls": len(lib.usable()), "routing": {"n": len(ROUTING), "correct": ok, "ms_each_after_cache": round(route_ms), "misses": miss, "tuned_on_this_set": True},
           "routing_holdout": {"n": len(HOLDOUT), "correct": h_ok, "misses": h_miss, "note": "written after tuning"},
           "self_tests": tests, "progressive_loading": {"all_in_one_prompt_tokens": one, "headers_only_tokens": head, "headers_plus_one_scroll_tokens": head + max(s.approx_tokens()[1] for s in lib.usable()),
                                                          "saving_percent": round(100 * (1 - (head + max(s.approx_tokens()[1] for s in lib.usable())) / one), 1)},
           "note": "token counts are a cheap estimate (characters / 3); routing set is author-written"}
    Path("docs/evidence").mkdir(exist_ok=True)
    Path("docs/evidence/scrolls.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("routing", "routing_holdout", "progressive_loading")}, indent=1)[:2600])
    print({k: f"{v['passed']}/{v['total']}" for k, v in tests.items()})


asyncio.run(main())
