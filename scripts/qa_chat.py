"""Broad behaviour QA: real local models, seeded memory, FAKE senders (nothing real is sent). Every utterance gets a check; prints failures.
Run: RAGNAROK_SKIP_SOURCES=telegram uv run python scripts/qa_chat.py [filter]"""
import asyncio, json, os, re, sys, tempfile, time
os.environ.setdefault("HF_HOME", os.path.expanduser("~/.cache/huggingface")); os.environ["HF_HUB_OFFLINE"] = "1"; os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp(); os.environ["HOME"] = os.environ["RAGNAROK_HOME"]; os.environ["RAGNAROK_SKIP_SOURCES"] = "telegram,slack,discord,gmail,gcal,gdrive,gmeet,outlook,outlook_cal,files,notifications"
sys.path.insert(0, "src")
from ragnarok.daemon.chat import ChatService, summarize  # noqa: E402
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

CASES = [  # (utterance, must_match_regex_or_None, must_not_match_regex_or_None, category)
 ("hi", r"hi|hello|hey", r"did not do anything", "talk"),
 ("hello there", r"hi|hello|hey", r"did not do anything", "talk"),
 ("how are you", r".", r"did not do anything|could not", "talk"),
 ("who are you", r"ragnarok|assistant|local", r"did not do anything", "talk"),
 ("thanks", r".", r"did not do anything", "talk"),
 ("what can you do", r"remind|email|calendar", None, "talk"),
 ("tell me a joke", r".{20}", r"did not do anything|could not", "talk"),
 ("explain what an API is in one line", r"interface|request|software|program|service", r"did not do anything", "talk"),
 ("what is the capital of France", r"paris", None, "talk"),
 ("give me tips to focus while studying", r".{40}", r"did not do anything", "talk"),
 ("i am stressed about the demo", r".{20}", r"did not do anything", "talk"),
 ("what is 18 percent of 42500", r"7[, ]?650", None, "calc"),
 ("what time is it", r"\d\d:\d\d", None, "calc"),
 ("what do you know about the budget review", r"thursday|3", r"don't have anything", "memory"),
 ("when is the budget review", r"thursday", None, "memory"),
 ("how much is invoice 8812", r"42,500", None, "memory"),
 ("who sent me the contract request", r"dev", None, "memory"),
 ("what did Ravi say", r"build|green|deploy", None, "memory"),
 ("what did Tushar message me", r"docs|mid eval|6:30", None, "memory"),
 ("what was said on discord", r"standup|ten", None, "memory"),
 ("anything from Mugdha", r"meeting|review|orion", None, "memory"),
 ("summarize everything we ingested", r"contract|invoice|build|standup", r"^\s*$", "summary"),
 ("catch me up", r"item|contract|invoice|build", None, "summary"),
 ("summarize my emails", r"contract|invoice|budget|meeting", None, "summary"),
 ("what happened on slack today", r"build|lunch", None, "summary"),
 ("give me a summary of all contexts", r"contract|invoice|build|standup", None, "summary"),
 ("what's on my calendar tomorrow", r"standup", None, "calendar"),
 ("what do i have tomorrow", r"standup", None, "calendar"),
 ("remind me to call mom tomorrow at 8am", r"reminder set", None, "act"),
 ("remind me in 2 hours to drink water", r"reminder set", None, "act"),
 ("set a reminder for friday 6pm gym", r"reminder set", None, "act"),
 ("put a call with Priya on my calendar tomorrow at 4pm", r"added to google calendar|calendar", None, "act-approve"),
 ("send an email to test@example.com saying the report is ready", r"email sent|sent", None, "act-approve"),
 ("email test@example.com asking for leave tomorrow", r"email sent|sent", None, "act-approve"),
 ("tell Tushar on telegram that the docs are pushed", r"posted|sent", None, "act-approve"),
 ("post in the slack general channel that the demo is live", r"posted|sent", None, "act-approve"),
 ("message the discord general channel that standup is at ten", r"posted|sent", None, "act-approve"),
 ("create a document in Drive called Plan saying ship on Monday", r"created|drive", None, "act-approve"),
 ("draft an email to dev@example.com about the contract", r"draft|refused", None, "act"),
 ("reply to Dev's email saying I will send it Thursday", r"email sent|sent|reply|refused", None, "act-approve"),
 ("run python to print the sum of 1 to 100", r"5050", None, "act"),
 ("do something with my files", None, r"error|traceback", "robust"),
 ("send it", None, r"error|traceback", "robust"),
 ("asdfgh", None, r"error|traceback", "robust"),
 ("", None, None, "robust"),
 ("explain my screen", r"screen|permission|capture|image", None, "vision"),
 ("what's on my screen", r"screen|permission|capture|image", None, "vision"),
 ("remember that I prefer morning meetings", r"remember|noted|got it|will", None, "memcmd"),
 ("what do you know about me", r"morning|prefer", None, "memcmd"),
 ("what did I ask you today", r".", None, "memcmd"),
 ("every day at 8am catch me up on my emails", r"trigger|every|8", None, "trigger"),
 ("list my triggers", r"8|every|trigger", None, "trigger"),
 ("do it", r"approvals|nothing is waiting", None, "security"),
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
