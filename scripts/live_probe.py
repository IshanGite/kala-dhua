"""Quick live probe of phrasings with real models. All approval cards are DENIED (nothing is sent). Prints tier, time, answer, cards, and flags for leaked reasoning or false success claims."""
import asyncio, os, sys, tempfile, time
os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, "src")
from ragnarok.daemon.chat import summarize
from ragnarok.daemon.wiring import build_world, warm_up

PHRASES = sys.argv[1:] or ["hi", "did someone ask me for a meeting on monday", "who sent it", "who asked me about the meet on discord and when", "send hi to ishan on telegram",
    "message Ishan Gite on telegram saying hi", "can you tell Ishan Gite on telegram that I am late", "send a message on discord saying hello", "send hello", "do it",
    "remind me tomorrow to call dad at 5pm", "put lunch with Meera on my calendar tomorrow at 1pm", "what's on telegram", "thanks", "who are you", "what is the capital of france"]


async def main():
    w = build_world(sandbox=False, max_cpu=101)
    await warm_up(w)
    for n in ("discord", "gmail", "gcal", "slack"):
        await w.runtime.runners[n].cycle()
    cards = []

    async def approver():
        while True:
            for c in w.services.hub.pending():
                cards.append(c["skill"]); w.services.hub.decide(c["id"], approve=False)
            await asyncio.sleep(0.05)
    t0 = asyncio.create_task(approver())
    hist = []
    for q in PHRASES:
        n0 = len(cards)
        t = time.perf_counter()
        turn = await w.agent.handle(q, history=hist[-8:])
        dt = time.perf_counter() - t
        o = summarize(turn)
        hist += [("user", q), ("assistant", o["text"][:300])]
        txt = o["text"]
        flags = []
        if "<think" in txt.lower() or "</think" in txt.lower() or txt.lower().startswith("okay, the user"):
            flags.append("LEAKED-REASONING")
        if any(p in txt.lower() for p in ("i have sent", "i've sent", "message sent", "i sent", "done!", "i will send", "i'll send")) and not o.get("state"):
            flags.append("POSSIBLE-FALSE-CLAIM")
        print(f"\nQ: {q}\n   [{turn.tier}/{turn.decision.backend} {dt:.1f}s] cards={cards[n0:]} {' '.join(flags)}\n   A: {txt[:240]}".replace("\n   A: ", "\n   A: "))
    t0.cancel()
    w.mem.close()

asyncio.run(main())
