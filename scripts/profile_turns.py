"""Where does a turn's time go? Real models, approval cards denied. Prints per-stage milliseconds for each phrasing, repeated for p50."""
import asyncio
import os
import statistics
import sys
import tempfile
import time

os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, "src")
from ragnarok.daemon.wiring import build_world, warm_up

CASES = sys.argv[1:] or ["remind me to call mom tomorrow at 8am", "what is on my calendar tomorrow", "what did I get on telegram", "message Ishan Gite on telegram saying hi", "how much is invoice 8812",
                         "hi", "draft an email to priya@acme.com about the invoice", "what is the budget review about"]


async def main():
    w = build_world(sandbox=False, max_cpu=101)
    await warm_up(w)
    for n in ("gmail", "gcal", "discord"):
        await w.runtime.runners[n].cycle()

    async def approver():
        while True:
            for c in w.services.hub.pending():
                w.services.hub.decide(c["id"], approve=False)
            await asyncio.sleep(0.02)
    t = asyncio.create_task(approver())
    for q in CASES:
        runs = []
        for _ in range(3):
            s = time.perf_counter()
            turn = await w.agent.handle(q)
            runs.append((round((time.perf_counter() - s) * 1000), turn))
        ms = [r[0] for r in runs]
        last = runs[-1][1]
        print(f"{statistics.median(ms):6.0f} ms  p-max {max(ms):5.0f}  {last.tier}/{last.decision.backend:<14} stages={last.timings}  | {q[:48]}")
    t.cancel()
    w.mem.close()

asyncio.run(main())
