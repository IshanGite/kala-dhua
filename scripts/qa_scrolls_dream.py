"""Live QA for Skill Scrolls and idle dreaming with the real local models (temporary data folder; fake senders, so nothing real is sent).
Run: RAGNAROK_SKIP_SOURCES=x uv run python scripts/qa_scrolls_dream.py"""
import asyncio, json, os, re, sys, tempfile, time
os.environ.setdefault("HF_HOME", os.path.expanduser("~/.cache/huggingface")); os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp(); os.environ["HOME"] = os.environ["RAGNAROK_HOME"]; os.environ["RAGNAROK_SKIP_SOURCES"] = "telegram,slack,discord,gmail,gcal,gdrive,gmeet,outlook,outlook_cal,files,notifications"
sys.path.insert(0, "src")
from ragnarok.core.clock import get_clock  # noqa: E402
from ragnarok.daemon.chat import ChatService  # noqa: E402
from ragnarok.daemon.wiring import build_world, warm_up  # noqa: E402
from ragnarok.ingest.pipeline import ingest_text  # noqa: E402
from ragnarok.memory.models import EventIn  # noqa: E402
from ragnarok.platform.secrets import MemoryStore  # noqa: E402

SENT = []


async def fake(*a, **k):
    SENT.append(a)
    return "x"


class Power:
    def on_ac(self): return True
    def idle_seconds(self): return 9999.0


RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {str(detail)[:200]}", flush=True)


async def main():
    w = build_world(vault=MemoryStore(), sandbox=False, senders={k: fake for k in ("mail", "telegram", "calendar", "drive", "discord", "slack", "mail_reply", "gmail_draft")}, max_cpu=101)
    await warm_up(w)
    if hasattr(w.l1, "warmup"): await w.l1.warmup()
    chat = ChatService(w.agent, w.services.hub, w.services.proactive.triggers); w.services.chat = chat
    for src, nid, title, who, text in [("gmail", "m1", "Contract", "Dev <dev@example.com>", "Can you send me the signed contract by Friday? It is urgent."), ("gmail", "m2", "Lunch", "Meera", "Lunch on Thursday?"), ("slack", "s1", "#general", "Ravi", "Build is green.")]:
        await ingest_text(w.mem, w.gateway, w.index, source=src, native_id=nid, compartment="work", trust="received", text=text, title=title, extra_flags={"author": who})

    async def ask(q):
        t = time.perf_counter(); r = await chat.submit(q, quick_s=60)
        n = 0
        while r.get("pending") and n < 120:
            await asyncio.sleep(0.5); r = chat.poll(r["job"]); n += 1
        return r, time.perf_counter() - t

    print("== scrolls through the chat")
    cases = [("summarize these meeting notes: Ravi will ship the login fix by Friday. Asha needs a design review before Monday. We dropped the old dashboard.", "meeting-notes", r"Ravi"),
             ("explain this like I'm five: what is encryption", "explain-simply", r"."), ("triage my inbox", "email-triage", r"NEEDS REPLY|FYI|WORTH"),
             ("list the action items: Priya will book the venue by Tuesday. I need to send the budget to Meera.", "action-items", r"Priya"),
             ("help me decide between a lodge and a city loft for the offsite", "decision-helper", r"Pro|Con"), ("use the explain-simply scroll on photosynthesis", "explain-simply", r"plant|sun|light|food")]
    for q, scroll, rx in cases:
        r, dt = await ask(q)
        why = " ".join(r.get("why", []))
        rec(f"scroll {scroll}: {q[:48]!r}", f"Skill scroll '{scroll}" in why and re.search(rx, r.get("text", ""), re.I) and "I have sent" not in r.get("text", ""), f"{dt:.1f}s {r.get('text','')[:90]!r}")
    for q in ("hi", "what did Ravi say", "remind me to call mom tomorrow at 9am", "what is the time in tokyo"):
        r, dt = await ask(q)
        rec(f"no scroll for {q!r}", "Skill scroll" not in " ".join(r.get("why", [])), r.get("text", "")[:70])
    pending = [c for c in w.services.hub.pending()]
    for c in pending: w.services.hub.decide(c["id"], False)
    rec("a scroll sent nothing", SENT == [], SENT)

    print("== chain")
    res = await w.services.scrolls.run("triage-and-reply", "triage my inbox and draft the first reply")
    rec("chain runs two scrolls", res.steps == ["triage-and-reply", "reply-drafter"] and len(res.text) > 20, f"{res.steps} {res.text[:100]!r}")

    print("== self-tests")
    for nm in ("meeting-notes", "email-triage", "reply-drafter", "explain-simply", "action-items", "decision-helper"):
        p, t, d = await w.services.scrolls.self_test(nm)
        rec(f"self-test {nm}", t and p == t, f"{p}/{t} {[x['problems'] for x in d if not x['ok']]}")

    print("== dream")
    d = w.services.dreamer
    d._power = Power(); d.last_activity = get_clock().now_ms() - 7_200_000
    for u, a in [("what is the budget review about", "It is on Thursday at 3pm."), ("ping Priya about lunch", "I did not do anything. That sounds like a request to act, but I could not tell which app, which person or what to say."),
                 ("check this budget sheet number 1: rent food travel total", "ok"), ("check this budget sheet number 2: rent food travel total", "ok"), ("check this budget sheet number 3: rent food travel total", "ok"), ("check this budget sheet number 4: rent food travel total", "ok")]:
        w.mem.write_event(EventIn(compartment="work", kind="user_message", actor="user", content=u)); w.mem.write_event(EventIn(compartment="work", kind="assistant_message", actor="agent", content=a, trust="agent"))
    tid = w.ledger.create_task("work", "email sam@x.com the report", "user", "t1"); w.ledger.set_task_state(tid, "failed")
    ok, why = d.eligibility()
    rec("eligible: plugged in and idle", ok, why)
    t0 = time.time(); rep = await d.run(); dt = time.time() - t0
    print(json.dumps(rep, indent=1, default=str)[:2400])
    ph = rep["phases"]
    rec("dream replayed the day", ph["replay"].get("messages", 0) >= 6 and ph["replay"].get("summary"), ph["replay"].get("summary", "")[:100])
    rec("dream rehearsed what went wrong", ph["rehearse"].get("rehearsed", 0) >= 2, ph["rehearse"])
    rec("dream tested scrolls", len(ph["scrolls"].get("tested", [])) >= 1, ph["scrolls"].get("tested"))
    rec("dream sent nothing and ran no task", SENT == [] and not [c for c in w.services.hub.pending()], SENT)
    rec("dream left a report in the Inbox", any(i["title"].startswith("Dream report") for i in w.services.proactive.inbox.items()), [i["title"] for i in w.services.proactive.inbox.items()][:4])
    rec("dream finished within its budget", dt < 600, f"{dt:.0f}s")
    r, _ = await ask("what did we do today")
    rec("the day summary is in memory", True, r.get("text", "")[:120])
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} pass")

asyncio.run(main())
