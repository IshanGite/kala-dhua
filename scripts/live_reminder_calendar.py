import asyncio, json, os, sys, tempfile, time
os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, "src")
from ragnarok.connectors.google import Calendar, GoogleAuth
from ragnarok.daemon.chat import summarize
from ragnarok.daemon.wiring import build_world, warm_up
from ragnarok.platform.secrets import default_store, token_name


async def main():
    w = build_world(sandbox=False, max_cpu=101)
    await warm_up(w)
    await w.runtime.runners["gcal"].cycle()
    cards = []

    async def approver():
        while True:
            for c in w.services.hub.pending():
                cards.append(c["skill"] + ":" + ",".join(f"{f['name']}={f['value']}" for f in c["fields"])); w.services.hub.decide(c["id"], approve=True)
            await asyncio.sleep(0.1)
    t = asyncio.create_task(approver())
    hist = []
    for q in ["set a reminder on 27 oct 7 pm for gym", "there was no reminder on calander", "what can you do", "what is on my calendar on 27 oct"]:
        s = time.perf_counter()
        turn = await w.agent.handle(q, history=hist)
        o = summarize(turn)
        hist += [("user", q), ("assistant", o["text"][:200])]
        print(f"\nQ: {q}\n   [{turn.tier}/{turn.decision.backend} {time.perf_counter()-s:.1f}s]\n   A: {o['text'][:330]}")
        if q.startswith("set a reminder"):
            await w.runtime.runners["gcal"].cycle()
    print("\nCARDS:", cards)
    t.cancel()
    g = json.loads(default_store().get(token_name("google", "read")))
    ra = GoogleAuth(g["client_id"], g["client_secret"], g["refresh_token"])
    ev = (await ra.get(Calendar.API, singleEvents="true", q="gym", timeMin="2026-10-27T00:00:00+05:30", timeMax="2026-10-28T00:00:00+05:30")).json().get("items", [])
    print("REAL CALENDAR:", [(e.get("summary"), e["start"].get("dateTime")) for e in ev])
    w.mem.close()

asyncio.run(main())
