import asyncio, os, sys, tempfile, time
os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, "src")
from ragnarok.daemon.chat import summarize
from ragnarok.daemon.wiring import build_world, warm_up

DRAFTS = []


async def fake_draft(d):
    DRAFTS.append(d)
    return "d1"


async def main():
    w = build_world(sandbox=False, max_cpu=101, senders={"gmail_draft": fake_draft})
    await warm_up(w)
    for n in ("gmail", "gcal"):
        await w.runtime.runners[n].cycle()

    async def approver():
        while True:
            for c in w.services.hub.pending():
                print("   >> CARD:", c["skill"], {f["name"]: str(f["value"])[:50] for f in c["fields"]}); w.services.hub.decide(c["id"], approve=False)
            await asyncio.sleep(0.1)
    t = asyncio.create_task(approver())
    for q in sys.argv[1:] or ["look up what Dev needs from me and remind me about it tomorrow at 9am",
                              "check what is on my calendar tomorrow and draft an email to priya@acme.com listing it",
                              "find the invoice email and remind me to pay it on 18 november at 10am"]:
        s = time.perf_counter()
        turn = await w.agent.handle(q)
        o = summarize(turn)
        print(f"\nQ: {q}\n   [{turn.tier}/{turn.decision.backend} {time.perf_counter()-s:.1f}s]")
        print("   plan/trace:", " | ".join(turn.decision.trace)[:400])
        print("   A:", o["text"][:360].replace("\n", "\n      "))
    t.cancel(); w.mem.close()

asyncio.run(main())
