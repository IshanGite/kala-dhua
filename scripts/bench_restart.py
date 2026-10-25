"""NFR3.2 measured for real: kill -9 the running app while a task waits for approval, restart it, and time until the state is back.
Uses a throwaway data folder and the real keychain key. Prints one JSON line (also saved to docs/evidence/nfr3_2_restart.json)."""
import json, os, signal, subprocess, sys, tempfile, time
import httpx

HOME = tempfile.mkdtemp()
PORT = 8791
ENV = {**os.environ, "RAGNAROK_HOME": HOME, "PYTHONPATH": "src", "USE_TF": "0"}


def start():
    return subprocess.Popen([sys.executable, "-m", "ragnarok.cli", "run", "--port", str(PORT), "--interval", "3600"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def token():
    p = os.path.join(HOME, "run", "session.token")
    for _ in range(200):
        if os.path.exists(p) and len(open(p, encoding="utf-8").read().strip()) > 20:
            return open(p, encoding="utf-8").read().strip()
        time.sleep(0.05)
    raise RuntimeError("no token")


def api(tok, path, body=None, timeout=30):
    h = {"x-ragnarok-token": tok}
    r = httpx.post(f"http://127.0.0.1:{PORT}{path}", json=body, headers=h, timeout=timeout) if body is not None else httpx.get(f"http://127.0.0.1:{PORT}{path}", headers=h, timeout=timeout)
    return r.json()


def wait_for(fn, limit=30):
    t = time.perf_counter()
    while time.perf_counter() - t < limit:
        try:
            v = fn()
            if v:
                return time.perf_counter() - t
        except Exception:
            pass
        time.sleep(0.05)
    return None


a = start()
try:
    tok = token()
except Exception:
    a.kill()
    raise
assert wait_for(lambda: httpx.get(f"http://127.0.0.1:{PORT}/healthz", timeout=1).status_code == 200, 40) is not None
r = api(tok, "/chat", {"text": "put a call with Priya on my calendar tomorrow at 4pm"}, timeout=15)  # waits for an approval click
assert wait_for(lambda: len(api(tok, "/approvals")["items"]) >= 1, 20) is not None, "the card never appeared"
os.kill(a.pid, signal.SIGKILL)                                                      # power loss
a.wait()
os.remove(os.path.join(HOME, "run", "session.token"))  # the new process writes its own
t0 = time.perf_counter()
b = start()
tok2 = token()
t_ready = wait_for(lambda: httpx.get(f"http://127.0.0.1:{PORT}/healthz", timeout=1).status_code == 200, 40)
t_card = wait_for(lambda: len(api(tok2, "/approvals")["items"]) == 1, 30)           # the interrupted task asks again
hist = api(tok2, "/trace")
status = api(tok2, "/status")
total = time.perf_counter() - t0
act = api(tok2, "/activity")["items"]
rec = [i for i in act if i["kind"] == "recovered"]
out = {"process_start_to_state_restored_s": round(total, 2), "interrupted_task_resumed_and_asking_again": t_card is not None, "recovered_activity": rec[:1], "gate_s": 5, "passes": total < 5 and t_card is not None}
print(json.dumps(out))
open("docs/evidence/nfr3_2_restart.json", "w", encoding="utf-8").write(json.dumps(out, indent=1))
b.send_signal(signal.SIGTERM)
