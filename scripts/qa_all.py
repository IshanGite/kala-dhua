"""Runs every live QA script several times and prints a pass table, so flaky behaviour shows up as a number. Real local models, fake senders (nothing real is sent).
Run: uv run python scripts/qa_all.py [rounds]   (about 8 minutes per round)"""
import os, re, subprocess, sys, time

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 2
SCRIPTS = ["chat", "wide", "nl", "edge", "multi", "scrolls_dream"]
env = {**os.environ, "RAGNAROK_SKIP_SOURCES": "x", "PYTHONUNBUFFERED": "1"}
table: dict[str, list[str]] = {s: [] for s in SCRIPTS}
fails: dict[str, set[str]] = {s: set() for s in SCRIPTS}
for r in range(ROUNDS):
    for s in SCRIPTS:
        name = f"scripts/qa_{s}.py"
        t = time.time()
        out = subprocess.run([sys.executable, "-u", name], capture_output=True, text=True, env=env, timeout=1800).stdout
        m = re.findall(r"(\d+)/(\d+) pass", out)
        table[s].append(f"{m[-1][0]}/{m[-1][1]}" if m else "ERROR")
        for line in out.splitlines():
            if line.startswith("FAILED:") or line.startswith("FAIL "):
                fails[s].add(line[:150])
        print(f"round {r + 1} {s:14} {table[s][-1]:8} {time.time() - t:5.0f}s", flush=True)
print("\nSUMMARY")
for s in SCRIPTS:
    print(f"{s:14}", table[s])
    for f in sorted(fails[s]):
        print("    ", f)
