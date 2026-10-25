"""Broad behaviour QA: real local models, seeded memory, FAKE senders (nothing real is sent). Every utterance gets a check; prints failures.
Run: RAGNAROK_SKIP_SOURCES=telegram uv run python scripts/qa_chat.py [filter]"""
import asyncio
import json
import os
import re
import sys
import tempfile
import time

os.environ.setdefault("HF_HOME", os.path.expanduser("~/.cache/huggingface")); os.environ["HF_HUB_OFFLINE"] = "1"; os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp(); os.environ["HOME"] = os.environ["RAGNAROK_HOME"]; os.environ["RAGNAROK_SKIP_SOURCES"] = "telegram,slack,discord,gmail,gcal,gdrive,gmeet,outlook,outlook_cal,files,notifications"
sys.path.insert(0, "src")
from ragnarok.daemon.chat import ChatService  # noqa: E402
from ragnarok.daemon.wiring import build_world, warm_up  # noqa: E402
from ragnarok.ingest.pipeline import ingest_text  # noqa: E402
from ragnarok.platform.secrets import MemoryStore, token_name  # noqa: E402

SENT = []
async def fake(kind, *a, **k):
    SENT.append((kind, a, k)); return "https://x.example/1"
def sender(kind):
    async def f(*a, **k): return await fake(kind, *a, **k)
    return f

async def seed(w):
    S = [("gmail", "m1", "Contract", "Dev <dev@example.com>", "Can you send me the signed contract by Friday? It is urgent.", "received"),
         ("gmail", "m2", "Budget review", "Meera", "The budget review is on Thursday at 3pm in room 4. Please bring the Q3 numbers.", "received"),
         ("gmail", "m3", "Invoice 8812", "Kestrel Supplies", "Invoice 8812 for 42,500 rupees is due on 20 November 2026.", "received"),
         ("gmail", "m4", "Set a meeting", "Mugdha", "Set a meeting for tomorrow for review of the Orion launch.", "received"),
         ("slack", "s1", "#general", "Ravi", "The build is green on main, deploy at 5.", "received"),
         ("slack", "s2", "#general", "Asha", "Lunch at the canteen on Friday?", "received"),
         ("discord", "d1", "broseph #general", "Ishan", "The standup moved to ten tomorrow.", "received"),
         ("telegram", "t1", "Ishan Gite", "Ishan Gite", "hello", "received"),
         ("telegram", "t2", "Tushar", "Tushar", "Did you push the docs? Mid eval is at 6:30.", "received"),
         ("gcal", "c1", "Standup", "", "Standup tomorrow 10:00. Where: Room 2", "received")]
    for src, nid, title, author, text, trust in S:
        await ingest_text(w.mem, w.gateway, w.index, source=src, native_id=nid, compartment="work", trust=trust, text=text, title=title, extra_flags=({"author": author, **({"from": re.search(r"<(.+)>", author).group(1)} if "<" in author else {})}) if author else None)




CASES = [
 # reminders and events: a card per write, nothing else sent
 ("remind me to submit the report on friday at 4", ["calendar"], None, "act"),
 ("ping me at 7pm to call dad", ["calendar"], None, "act"),
 ("wake me up at 6am tomorrow", ["calendar"], None, "act"),
 ("remind me in an hour to check the oven", ["calendar"], None, "act"),
 ("block tomorrow 2pm for deep work", ["calendar"], None, "act"),
 ("schedule lunch with Priya on friday at 1pm", ["calendar"], None, "act"),
 ("add dentist appointment next tuesday 5pm to my calendar", ["calendar"], None, "act"),
 # email
 ("mail dev@example.com that I will send the contract tomorrow", ["mail"], None, "act"),
 ("email sam@example.com asking if he is free on friday", ["mail"], None, "act"),
 ("send a thank you email to meera@example.com", ["mail"], None, "act"),
 ("reply to Dev saying tomorrow 11 works", ["mail_reply"], None, "act"),
 # chats
 ("tell the discord general channel that standup is moved to ten", ["discord"], None, "act"),
 ("dm Tushar on telegram: where are you", ["telegram"], None, "act"),
 ("let Ravi know on slack that I am 5 min late", ["slack"], None, "act"),
 ("post in slack general that the build is green", ["slack"], None, "act"),
 # drive
 ("create a doc named Ideas with three startup ideas", ["drive"], None, "act"),
 ("save a note in drive called groceries saying milk eggs bread", ["drive"], None, "act"),
 # reading
 ("what did Mugdha ask me", r"meeting|review|orion", None, "read"),
 ("any messages from Tushar?", r"docs|eval", None, "read"),
 ("when is my next meeting", r"next event|no upcoming", None, "read"),
 ("did anyone mention the budget", r"budget|thursday", None, "read"),
 ("what is the invoice amount", r"42,500", None, "read"),
 ("is there anything about lunch", r"lunch|canteen", None, "read"),
 # conversation
 ("thanks!", r".", r"did not do anything|error", "talk"),
 ("ok cool", r".", r"did not do anything|error", "talk"),
 ("what should I eat for dinner", r".{20}", r"did not do anything|error", "talk"),
 ("translate good morning to french", r"bonjour|bon jour", None, "talk"),
 ("explain recursion simply", r"itself|function|smaller", None, "talk"),
 ("give me a motivational line", r".{15}", r"did not do anything", "talk"),
 # memory commands
 ("remember that I am allergic to peanuts", r"remember|noted|got it|will|peanut", None, "mem"),
 ("what am I allergic to", r"peanut", None, "mem"),
 # exact answers by code
 ("What is the time in berlin right now", r"in Berlin", r"IST", "util"),
 ("berlin time", r"in Berlin", None, "util"),
 ("what time is it in new york", r"New York", None, "util"),
 ("how many days until christmas", r"day\(s\) until", None, "util"),
 ("what day is 15 november", r"Sunday", None, "util"),
 ("convert 5 km to miles", r"3\.1", None, "util"),
 ("what's the weather in paris", r"offline", None, "util"),
 # careful ones that must NOT send
 ("should I email Dev about the contract", None, r"email sent", "safe"),
 ("what happens if I tell Ravi on telegram that I quit", None, r"posted", "safe"),
 ("can you remind me things?", None, r"reminder set", "safe"),
]
async def main():
    flt = sys.argv[1] if len(sys.argv) > 1 else ""
    vault = MemoryStore(); vault.set(token_name("google", "write"), json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))
    senders = {k: sender(k) for k in ("email", "mail", "calendar", "doc", "drive", "telegram", "discord", "slack", "chat", "reply", "mail_reply")}
    w = build_world(vault=vault, sandbox=True, senders=senders, max_cpu=101)
    await warm_up(w)
    if hasattr(w.l1, "warmup"): await w.l1.warmup()
    await seed(w)
    chat = ChatService(w.agent, w.services.hub, w.services.proactive.triggers); w.services.chat = chat
    cards = []
    async def approver():
        while True:
            for c in w.services.hub.pending():
                cards.append(c["skill"]); w.services.hub.decide(c["id"], approve=True)
            await asyncio.sleep(0.05)
    task = asyncio.create_task(approver())
    fails, rows = [], []
    for q, yes, no, cat in CASES:
        if flt and flt not in cat and flt not in q: continue
        t = time.perf_counter()
        try:
            n0 = len(SENT)
            r = await asyncio.wait_for(chat.submit(q), 150)
            n = 0
            while r.get("pending") and n < 120:
                await asyncio.sleep(0.5); r = chat.poll(r["job"]); n += 1
        except Exception as e:  # noqa: BLE001
            r = {"text": f"EXC {type(e).__name__}: {e}"}
        dt = time.perf_counter() - t
        text = (r.get("text") or "")
        sent = [k for k, a, _ in SENT[n0:]]
        want = yes if isinstance(yes, list) else None
        ok = not text.startswith("EXC")
        if want is not None:
            ok = ok and sent == want
        elif isinstance(yes, str):
            ok = ok and bool(re.search(yes, text, re.I)) and not sent
        if isinstance(no, str):
            ok = ok and not re.search(no, text, re.I)
        text = f"{text} | SENT={sent} | LAST={str(SENT[-1][1])[:260] if SENT else ''}"
        rows.append((cat, q, ok, dt))
        print(f"{'PASS' if ok else 'FAIL'} [{cat}] {dt:5.1f}s  {q[:60]!r}\n        -> {text[:230]!r}", flush=True)
        if not ok: fails.append((cat, q, text[:200]))
    task.cancel()
    print(f"\n{len(rows)-len(fails)}/{len(rows)} pass;  slowest:", sorted([(round(d,1), q[:40]) for c, q, o, d in rows], reverse=True)[:6])
    print("cards approved:", cards); print("fake sends:", [(k, str(a)[:70]) for k, a, _ in SENT])
    for f in fails: print("FAILED:", f)
asyncio.run(main())
