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
 ("set a reminder on my calendar for tomorrow at 9 pm for final eval round", r"reminder set", r"your calendar from", "nl-act"),
 ("send a message on discord on broseph aying valo cancel?", r"posted", r"leaving me out", "nl-act"),
 ("remind me tmrw 8am to call mom", r"reminder set", None, "nl-act"),
 ("yo remind me in half an hour to stretch", r"reminder set", None, "nl-act"),
 ("can u text Tushar on tg that im omw", r"posted", None, "nl-act"),
 ("tell tushar the demo is ready", r"posted|which app|who", None, "nl-act"),
 ("shoot sam@example.com a note that the report is done", r"email sent|sent", None, "nl-act"),
 ("put dentist friday 5pm in my calendar", r"added to google calendar|calendar", None, "nl-act"),
 ("dont forget to buy milk at 6pm today", r"reminder set", None, "nl-act"),
 ("message priya on slack general: demo at 5, be there", r"posted", None, "nl-act"),
 ("what's my schedule tomorrow", r"standup|calendar", None, "nl-read"),
 ("anything important today?", r".", r"did not do anything", "nl-read"),
 ("what's up with the contract", r"contract|friday|dev", None, "nl-read"),
 ("did Tushar text me", r"docs|eval|tushar", None, "nl-read"),
 ("recuerdame llamar al banco manana a las 5", r"reminder set", None, "multi-lang"),
 ("envoie un message a Ravi sur telegram: je suis en retard", r"posted|ravi|conversation", None, "multi-lang"),
 ("कल सुबह 9 बजे मुझे जिम जाने की याद दिलाओ", r"reminder set", None, "multi-lang"),
 ("kal subah 9 baje mujhe gym ki yaad dilana", r"reminder set", None, "multi-lang"),
 ("wie spaet ist es", r"\d\d:\d\d|time", None, "multi-lang"),
 ("hola, como estas?", r".", r"did not do anything|error", "multi-lang"),
 ("resume mes emails", r"contract|invoice|budget|item", None, "multi-lang"),
]
async def main():
    flt = sys.argv[1] if len(sys.argv) > 1 else ""
    vault = MemoryStore(); vault.set(token_name("google", "write"), json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))
    senders = {k: sender(k) for k in ("email", "mail", "calendar", "doc", "drive", "telegram", "discord", "slack", "chat", "reply")}
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
            r = await asyncio.wait_for(chat.submit(q), 90)
            n = 0
            while r.get("pending") and n < 120:
                await asyncio.sleep(0.5); r = chat.poll(r["job"]); n += 1
        except Exception as e:  # noqa: BLE001
            r = {"text": f"EXC {type(e).__name__}: {e}"}
        dt = time.perf_counter() - t
        text = (r.get("text") or "")
        ok = (yes is None or re.search(yes, text, re.I)) and (no is None or not re.search(no, text, re.I)) and not text.startswith("EXC") and (q == "" or bool(text.strip()))
        rows.append((cat, q, ok, dt))
        print(f"{'PASS' if ok else 'FAIL'} [{cat}] {dt:5.1f}s  {q[:60]!r}\n        -> {text[:230]!r}", flush=True)
        if not ok: fails.append((cat, q, text[:200]))
    task.cancel()
    print(f"\n{len(rows)-len(fails)}/{len(rows)} pass;  slowest:", sorted([(round(d,1), q[:40]) for c, q, o, d in rows], reverse=True)[:6])
    print("cards approved:", cards); print("fake sends:", [(k, str(a)[:70]) for k, a, _ in SENT])
    for f in fails: print("FAILED:", f)
asyncio.run(main())
