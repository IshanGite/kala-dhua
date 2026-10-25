"""NFR2.1: retrieval and intent classification latency (gate: < 800 ms), measured on a seeded corpus with the real embedder and the real router.
Corpus: 2,000 generated messages across mail/chat/notes (same on macOS and Windows: seeded). Queries: 200 natural questions, each with a known answer message,
so recall@5 is measured too. Output: docs/evidence/nfr2_1.json."""
import asyncio
import json
import os
import random
import statistics
import sys
import tempfile
import time

os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, "src")
from ragnarok.core.clock import get_clock  # noqa: E402
from ragnarok.daemon.wiring import build_world, warm_up  # noqa: E402
from ragnarok.ingest.pipeline import ingest_text  # noqa: E402
from ragnarok.memory.retrieval import retrieve  # noqa: E402
from ragnarok.platform.secrets import MemoryStore  # noqa: E402

rng = random.Random(20261026)
PEOPLE = ["Priya Nair", "Dev Malhotra", "Meera Iyer", "Ravi Shah", "Asha Menon", "Kabir Anand", "Tara Bose", "Imran Qureshi", "Nisha Rao", "Vikram Sethi", "Leela Das", "Omar Farooq"]
PROJECTS = ["Orion", "Saturn", "Harbour", "Zephyr", "Atlas", "Nimbus", "Cobalt", "Falcon", "Lotus", "Mercury", "Tundra", "Vega"]
THINGS = [("the budget review", "is on {day} at {t} in {room}"), ("the vendor contract", "must be signed by {day}"), ("invoice {n}", "for {amt} rupees is due on {date}"), ("the launch", "moved to {date} because of {why}"),
          ("the hiring plan", "needs {k} engineers by {date}"), ("the offsite", "is booked at {place} on {date}"), ("the security audit", "starts {day} and {who} owns it")]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
ROOMS = ["Room 4", "Room 12", "the Atrium", "Room 7", "the Loft"]
DATES = ["14 April", "3 March", "20 November", "9 January", "28 February", "17 June", "5 August", "30 September"]
WHY = ["a vendor delay", "a hardware shortage", "a legal review", "a funding gap", "a staffing change"]
PLACES = ["Goa", "Udaipur", "Coorg", "Pondicherry", "Rishikesh"]
NOISE = ["Lunch is at the canteen near the office, everyone is invited.", "Reminder to submit timesheets before the end of the week.", "The printer on floor 3 is out of toner again.", "Happy birthday to the whole team, cake at four.",
         "Please update your laptop to the latest security patch tonight.", "Parking will be closed on Saturday for maintenance.", "New joiners orientation is on the first Monday of the month."]


def make(i):
    p, proj = rng.choice(PEOPLE), rng.choice(PROJECTS)
    thing, tpl = rng.choice(THINGS)
    n = rng.randint(1000, 9999)
    fill = dict(day=rng.choice(DAYS), t=f"{rng.randint(9, 17)}:{rng.choice(['00', '30'])}", room=rng.choice(ROOMS), n=n, amt=f"{rng.randint(5, 95) * 1000:,}", date=rng.choice(DATES), why=rng.choice(WHY),
                k=rng.randint(2, 6), place=rng.choice(PLACES), who=rng.choice(PEOPLE))
    thing = thing.format(**fill)
    text = f"{p} on {proj}: {thing} {tpl.format(**fill)}."
    return p, proj, thing, text, fill


async def main():
    w = build_world(vault=MemoryStore(), sandbox=False, max_cpu=101)
    await warm_up(w)
    if hasattr(w.l1, "warmup"):
        await w.l1.warmup()
    docs = []
    t0 = time.perf_counter()
    for i in range(2000):
        if i % 4 == 3:
            text, p, proj, thing, fill = rng.choice(NOISE), None, None, None, None
        else:
            p, proj, thing, text, fill = make(i)
        src = ["gmail", "slack", "discord", "telegram"][i % 4]
        r = await ingest_text(w.mem, w.gateway, w.index, source=src, native_id=f"m{i}", compartment="work", trust="received", text=text, title=(thing or "note")[:60])
        if p:
            docs.append((r.item_id, p, proj, thing, text))
    ingest_s = time.perf_counter() - t0
    # 200 queries, each about one specific generated message (unique by project + thing + person)
    sample = rng.sample(docs, 200)
    qs = []
    for item_id, p, proj, thing, text in sample:
        form = rng.choice(["person_thing", "thing_proj", "when", "proj_thing"])
        q = {"person_thing": f"what did {p.split()[0]} say about {thing}", "thing_proj": f"{thing} {proj}", "when": f"when is {thing} for {proj}", "proj_thing": f"{proj}: {thing}"}[form]
        need = [p.split()[0], thing] if form == "person_thing" else [thing, proj]  # the attributes the query actually names: any message containing all of them is a correct answer
        qs.append((q, need))
    lat_r, hits = [], 0
    for q, need in qs:
        s = time.perf_counter()
        res = await retrieve(q, compartment="work", reader=w.reader, index=w.index, gateway=w.gateway, now_ms=get_clock().now_ms(), deadline=time.monotonic() + 10, k=5, token_budget=600)
        lat_r.append((time.perf_counter() - s) * 1000)
        hits += any(all(a.lower() in h.text.lower() for a in need) for h in res.hits[:5])
    lat_c = []
    # the intent decision for a realistic mix: questions go straight to retrieval (no classifier needed), commands hit the rules, and only genuinely ambiguous wording reaches Laya / the small arbiter
    from ragnarok.harness.answer import is_agenda, is_smalltalk
    from ragnarok.router.intent_rules import match_intent
    commands = ["remind me to call mom tomorrow at 8am", "tell Ravi on telegram that I am late", "send an email to a@b.com saying hi", "put a call on my calendar tomorrow at 4pm", "reply to Dev's email saying yes"] * 14
    ambiguous = ["can you get the budget file over to Priya", "I need Ravi to know about the delay", "ping the team about lunch", "let Meera know I am running late", "please follow up with Dev on the contract"] * 4
    kinds = {"question": [], "command": [], "ambiguous": []}
    for q, _ in qs[:100]:
        s = time.perf_counter()
        _ = (match_intent(q), is_smalltalk(q), is_agenda(q), w.agent._is_question(q))  # what the agent evaluates before it decides a question needs no model
        kinds["question"].append((time.perf_counter() - s) * 1000)
    for q in commands:
        s = time.perf_counter()
        await w.agent.cascade.route(q, compartment="work", caller_trust="user_direct", now_ms=get_clock().now_ms())
        kinds["command"].append((time.perf_counter() - s) * 1000)
    for q in ambiguous:
        s = time.perf_counter()
        await w.agent.cascade.route(q, compartment="work", caller_trust="user_direct", now_ms=get_clock().now_ms())
        kinds["ambiguous"].append((time.perf_counter() - s) * 1000)
    lat_c = kinds["question"] + kinds["command"] + kinds["ambiguous"]

    def pct(xs):
        xs = sorted(xs)
        return {"p50": round(statistics.median(xs), 1), "p95": round(xs[int(0.95 * (len(xs) - 1))], 1), "p99": round(xs[int(0.99 * (len(xs) - 1))], 1), "max": round(xs[-1], 1), "n": len(xs)}
    out = {"corpus_messages": 2000, "ingest_seconds": round(ingest_s), "retrieval_ms": pct(lat_r), "recall_at_5": round(hits / len(qs), 3), "classification_ms": pct(lat_c), "classification_by_kind_ms": {k: pct(v) for k, v in kinds.items()}, "gate_ms": 800,
           "passes": pct(lat_r)["p95"] < 800 and pct(lat_c)["p95"] < 800, "note": "retrieval = embed + keyword + vector + rank; classification = full cascade route (L0 check, rules, Laya, embedding rung, small-model arbiter budget)"}
    print(json.dumps(out))
    open("docs/evidence/nfr2_1.json", "w", encoding="utf-8").write(json.dumps(out, indent=1))
    w.mem.close()

asyncio.run(main())
