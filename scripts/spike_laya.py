"""Spike 9: Laya on this machine. CPU vs MPS latency for 1 and 10 questions, resident memory, offline load, no open port.
Usage: python scripts/spike_laya.py cpu|mps   (prints one JSON line; run each device in its own process for a clean memory number)."""
import json
import os
import socket
import statistics
import sys
import time

os.environ.setdefault("USE_TF", "0")
os.environ["HF_HUB_OFFLINE"] = "1"  # proves it loads without the network after the first download
import psutil  # noqa: E402

device = sys.argv[1]
import laya  # noqa: E402

proc = psutil.Process()
t = time.perf_counter()
agent = laya.load("convaiinnovations/laya", subfolder="typed-decisions", device=device)
load_s = time.perf_counter() - t
TEXT = "Can you move my meeting with Priya to Friday and email her the updated invoice? She said it was urgent."
Q1 = {"intent": {"type": "choice", "instructions": "What does the user want?", "criteria": {"read": "look up information", "write": "change or send something", "chat": "small talk"}}}
Q10 = {f"q{i}": {"type": "noul", "instructions": ins} for i, ins in enumerate([
    "Does this ask to send something to another person?", "Is this urgent?", "Does it involve money?", "Does it change a calendar?", "Is it only a question?",
    "Does it ask to delete data?", "Does it mention a deadline?", "Is it about a named person?", "Does it need attachments?", "Is it ambiguous?"])}
Q10["intent"] = Q1["intent"]
Q10.pop("q9")


def timed(spec, n):
    agent.predict(TEXT, spec)  # warm
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        agent.predict(TEXT, spec)
        xs.append((time.perf_counter() - t0) * 1000)
    return {"p50_ms": round(statistics.median(xs), 1), "max_ms": round(max(xs), 1), "n": n}


r1, r10 = timed(Q1, 15), timed(Q10, 10)
listening = [c.laddr.port for c in proc.net_connections(kind="inet") if c.status == "LISTEN"]
print(json.dumps({"device": device, "load_s": round(load_s, 1), "one_question": r1, "ten_questions": r10, "rss_mb": round(proc.memory_info().rss / 1e6),
                  "listening_ports": listening, "offline_load": True, "python": sys.version.split()[0], "host": socket.gethostname()[:0]}))
