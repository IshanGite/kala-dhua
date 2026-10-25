"""Live agent check with the REAL local models (Laya + Ollama), fake senders (nothing leaves the machine). Prints routing tier, backend, latency, outcome."""
import asyncio
import os
import sys
import tempfile
import time

os.environ["RAGNAROK_SECRETS"] = "memory"
os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, "src")
from ragnarok.daemon.chat import summarize  # noqa: E402
from ragnarok.daemon.wiring import build_world, warm_up  # noqa: E402
from ragnarok.ingest.pipeline import ingest_text  # noqa: E402
from ragnarok.platform.secrets import MemoryStore  # noqa: E402

SENT = []


async def fake_post(chat_id, text):
    SENT.append(("post", chat_id, text))
    return "m1"


async def fake_mail(p):
    SENT.append(("mail", p["to"], p["subject"]))
    return "gm1"


async def main():
    w = build_world(vault=MemoryStore(), sandbox=False, senders={"telegram": fake_post, "discord": fake_post, "mail": fake_mail}, max_cpu=101)
    await warm_up(w)
    if hasattr(w.l1, "warmup"):
        await w.l1.warmup()
    for native, title, text in [("general:1", "broseph #general", "Standup moved to ten tomorrow, bring the Orion slides"), ("general:2", "broseph #general", "The vendor invoice 4471 is due on 12 November"),
                                ("55:9", "Ravi", "Can you confirm the harbour permit renewal date?")]:
        src = "telegram" if title == "Ravi" else "discord"
        await ingest_text(w.mem, w.gateway, w.index, source=src, native_id=native, compartment="work", trust="received", text=text, title=title)

    async def approver():
        while True:
            for card in w.services.hub.pending():
                print("   >> APPROVAL CARD:", card.get("skill"), "| tier", card.get("tier"), "| warnings", card.get("warnings"), "| preview", str(card.get("dry_run"))[:160])
                w.services.hub.decide(card["id"], APPROVE)
            await asyncio.sleep(0.2)

    global APPROVE
    APPROVE = True
    task = asyncio.create_task(approver())
    reqs = sys.argv[1:] or [
        "remind me to call the bank tomorrow at 5pm", "what reminders do I have open", "what was said in the general channel about the standup",
        "tell Ravi on telegram that I will be late", "draft an email to priya@acme.com about the invoice 4471",
        "send an email to priya@acme.com saying the invoice 4471 is attached", "who is the president of france",
    ]
    for r in reqs:
        t = time.perf_counter()
        turn = await w.agent.handle(r)
        out = summarize(turn)
        print(f"\nQ: {r}\n   tier={turn.tier} backend={turn.decision.backend} family={turn.decision.intent_family} skills={turn.decision.skills} total={time.perf_counter()-t:.1f}s route_ms={turn.decision.latency_ms:.0f}")
        print("   A:", out["text"][:260], "| learned:", turn.learned)
    print("\nSENT (fake):", SENT)
    task.cancel()
    w.mem.close()

asyncio.run(main())
