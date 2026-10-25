"""Sustained load for NFR2.2: back-to-back questions, briefings and drafts on the real models for N seconds (default 50). Run the sampler on this process's pid."""
import asyncio
import os
import sys
import tempfile
import time

os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, "src")
from ragnarok.daemon.wiring import build_world, warm_up  # noqa: E402
from ragnarok.ingest.pipeline import ingest_text  # noqa: E402
from ragnarok.platform.secrets import MemoryStore  # noqa: E402

SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 50
QS = ["what is the budget review about", "catch me up on everything from today", "who asked about the launch", "how much is invoice 8812", "draft an email to a@b.com about the launch delay", "what did Priya say about the hiring plan"]


async def main():
    w = build_world(vault=MemoryStore(), sandbox=False, max_cpu=101, senders={"gmail_draft": lambda d: asyncio.sleep(0, "d")})
    await warm_up(w)
    for i in range(150):
        await ingest_text(w.mem, w.gateway, w.index, source=["gmail", "slack", "discord"][i % 3], native_id=f"m{i}", compartment="work", trust="received", text=f"Priya on Orion: the budget review is on Thursday at 3pm; launch moved to {14 + i % 9} April because of a vendor delay; invoice 8812 for 42,500 rupees; hiring plan needs {2 + i % 4} engineers.", title="Orion update")
    print("READY", os.getpid(), flush=True)
    await asyncio.sleep(6)  # let the sampler attach
    t_end, n = time.time() + SECONDS, 0
    while time.time() < t_end:
        await w.agent.handle(QS[n % len(QS)])
        n += 1
    print("DONE requests:", n, flush=True)
    w.mem.close()

asyncio.run(main())
