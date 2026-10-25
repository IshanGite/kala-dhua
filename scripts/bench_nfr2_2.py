"""NFR2.2: the always-on agent plus local model inference must not use more than 30% of the machine. Samples the running app (and Ollama) every few seconds.
Usage: python scripts/bench_nfr2_2.py <pid> <seconds> ; writes docs/evidence/nfr2_2.json. 'machine share' = process CPU % divided by the number of logical cores (so 100% = every core busy)."""
import json, statistics, sys, time
import psutil

pid, seconds = int(sys.argv[1]), int(sys.argv[2])
cores = psutil.cpu_count()
proc = psutil.Process(pid)
ollama = [p for p in psutil.process_iter(["name", "cmdline"]) if "ollama" in (p.info["name"] or "").lower()]
for p in [proc, *ollama]:
    p.cpu_percent(None)
psutil.cpu_percent(None)
rows = []
t_end = time.time() + seconds
while time.time() < t_end:
    time.sleep(5)
    app = proc.cpu_percent(None) / cores + sum(c.cpu_percent(None) for c in proc.children(recursive=True)) / cores
    oll = sum(p.cpu_percent(None) for p in ollama if p.is_running()) / cores
    rows.append({"t": round(time.time() - (t_end - seconds)), "app": round(app, 2), "ollama": round(oll, 2), "system": psutil.cpu_percent(None), "rss_mb": round(proc.memory_info().rss / 1e6)})
tot = [r["app"] + r["ollama"] for r in rows]
out = {"seconds": seconds, "cores": cores, "samples": len(rows), "agent_plus_models_machine_share_pct": {"mean": round(statistics.mean(tot), 2), "p95": round(sorted(tot)[int(0.95 * (len(tot) - 1))], 2), "max": round(max(tot), 2)},
       "agent_alone_mean_pct": round(statistics.mean(r["app"] for r in rows), 2), "gate_pct": 30, "passes": max(tot) < 30, "rss_mb_max": max(r["rss_mb"] for r in rows)}
print(json.dumps(out))
open("docs/evidence/nfr2_2.json", "w", encoding="utf-8").write(json.dumps({"summary": out, "trace": rows}, indent=1))
