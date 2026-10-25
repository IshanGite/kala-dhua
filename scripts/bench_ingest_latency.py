"""How fast does a new email reach the agent's memory? Starts the app (live polling defaults), sends a real email to the user's own inbox, polls /messages until it appears. Telegram is excluded so the real bot queue is never consumed."""
import json
import os
import subprocess
import sys
import tempfile
import time

import httpx

HOME = tempfile.mkdtemp()
PORT = 8796
ENV = {**os.environ, "RAGNAROK_HOME": HOME, "PYTHONPATH": "src", "USE_TF": "0", "RAGNAROK_SKIP_SOURCES": "telegram"}
p = subprocess.Popen([sys.executable, "-m", "ragnarok.cli", "run", "--port", str(PORT)], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    tf = os.path.join(HOME, "run", "session.token")
    for _ in range(300):
        if os.path.exists(tf) and len(open(tf, encoding="utf-8").read().strip()) > 20:
            break
        time.sleep(0.1)
    tok = open(tf, encoding="utf-8").read().strip()
    H = {"x-ragnarok-token": tok}
    base = f"http://127.0.0.1:{PORT}"
    for _ in range(200):
        try:
            if httpx.get(base + "/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    time.sleep(25)  # let the first sync of every source finish
    sys.path.insert(0, "src")
    import asyncio

    from ragnarok.connectors.google import Gmail, GoogleAuth, build_raw
    from ragnarok.connectors.http import request
    from ragnarok.platform.secrets import default_store, token_name

    async def send(marker):
        g = json.loads(default_store().get(token_name("google", "write")))
        gr = json.loads(default_store().get(token_name("google", "read")))
        w, r = GoogleAuth(g["client_id"], g["client_secret"], g["refresh_token"]), GoogleAuth(gr["client_id"], gr["client_secret"], gr["refresh_token"])
        me = (await r.get(f"{Gmail.API}/profile")).json()["emailAddress"]
        await request(w.http, "POST", f"{Gmail.API}/messages/send", headers={"Authorization": f"Bearer {await w.token()}"}, json={"raw": build_raw(me, f"Latency test {marker}", f"Latency test body {marker}: can you confirm?", auto=False)})

    lat = []
    for i in range(3):
        marker = f"zq{int(time.time())}{i}"
        t0 = time.perf_counter()
        asyncio.run(send(marker))
        sent = time.perf_counter() - t0
        t1 = time.perf_counter()
        seen = None
        while time.perf_counter() - t1 < 60:
            items = httpx.get(f"{base}/messages?limit=5&q={marker}", headers=H, timeout=5).json().get("items", [])
            if items:
                seen = time.perf_counter() - t1
                break
            time.sleep(0.5)
        lat.append(round(seen, 1) if seen else None)
        time.sleep(3)
    out = {"gmail_arrival_to_memory_s": lat, "poll_interval_gmail_s": 8, "note": "includes Gmail's own delivery delay (about 1 to 3 s) and the polling interval"}
    print(json.dumps(out))
    open("docs/evidence/ingest_latency.json", "w", encoding="utf-8").write(json.dumps(out, indent=1))
finally:
    p.terminate()
