"""FR4 live: a user-defined trigger and a schedule, then a real email arrives; the agent notices on its own, with a macOS notification. Uses a throwaway store, the real accounts and models."""
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, "src")
from ragnarok.connectors.google import Gmail, GoogleAuth, build_raw  # noqa: E402
from ragnarok.connectors.http import request  # noqa: E402
from ragnarok.daemon.wiring import build_world, warm_up  # noqa: E402
from ragnarok.platform.secrets import default_store, token_name  # noqa: E402


def show(inbox, since=0):
    for i in inbox.items()[: max(0, len(inbox.items()) - since)]:
        print(f"   [{i['kind']}] {i['title']} :: {i['body'][:110]!r}")


async def main():
    w = build_world(sandbox=False, max_cpu=101)
    await warm_up(w)
    pro = w.services.proactive
    pro.configure(briefing_at=None, os_notify=True)
    print(pro.triggers.parse("when I get an email from Ishan, tell me"))
    at = (datetime.now() + timedelta(seconds=70)).strftime("%H:%M")
    print(pro.triggers.parse(f"every day at {at} catch me up on my emails"))
    await w.runtime.runners["gmail"].cycle()
    await pro.run()
    print("baseline inbox items:", len(pro.inbox.items()), "(history is not announced)")
    g = json.loads(default_store().get(token_name("google", "write")))
    gr = json.loads(default_store().get(token_name("google", "read")))
    wr, rd = GoogleAuth(g["client_id"], g["client_secret"], g["refresh_token"]), GoogleAuth(gr["client_id"], gr["client_secret"], gr["refresh_token"])
    me = (await rd.get(f"{Gmail.API}/profile")).json()["emailAddress"]
    await request(wr.http, "POST", f"{Gmail.API}/messages/send", headers={"Authorization": f"Bearer {await wr.token()}"},
                  json={"raw": build_raw(me, "[Ragnarok test] venue", "Hi, can you confirm the venue for the offsite by Friday? We are waiting for your answer.", auto=False)})
    print("sent a test email that asks a question")
    await asyncio.sleep(6)
    await w.runtime.runners["gmail"].cycle()
    await pro.run()
    print("after the email arrived:")
    show(pro.inbox)
    print("waiting for the schedule to fire at", at)
    t = time.time()
    while time.time() - t < 150 and not any(i["title"].startswith("Result:") for i in pro.inbox.items()):
        await asyncio.sleep(8)
        await pro.run()
    print("after the schedule:")
    show(pro.inbox)
    w.mem.close()

asyncio.run(main())
