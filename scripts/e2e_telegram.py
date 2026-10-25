import asyncio
import sys

sys.path.insert(0, "src")
from ragnarok.daemon.chat import summarize
from ragnarok.daemon.wiring import build_world, warm_up


async def main():
    w = build_world(sandbox=False, max_cpu=101)
    await warm_up(w)
    q = w.runtime.runners["telegram"]._q
    print("revived dead telegram rows:", q.revive("E_WRONG_SOURCE"))
    rep = await w.runtime.runners["telegram"].cycle()
    print("telegram cycle:", rep)
    print("telegram items now:", [(i["title"], i["preview"][:40]) for i in w.reader.recent_items(limit=10, source="telegram")])
    allow = {"on": False}

    async def approver():
        while True:
            for c in w.services.hub.pending():
                print("   >> CARD:", c["skill"], {f["name"]: str(f["value"])[:70] for f in c.get("fields", [])})
                w.services.hub.decide(c["id"], approve=allow["on"])
            await asyncio.sleep(0.1)
    t = asyncio.create_task(approver())
    for qn, ok in [("what did I send on telegram", False), ("tell Ishan Gite on telegram that Ragnarok test: this message was sent by the local agent", True)]:
        allow["on"] = ok
        turn = await w.agent.handle(qn)
        o = summarize(turn)
        print(f"\nQ: {qn}\n   [{turn.tier}/{turn.decision.backend}]\n   A: {o['text'][:300]}")
    t.cancel()
    w.mem.close()

asyncio.run(main())
