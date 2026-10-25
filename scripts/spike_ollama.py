"""Spike 2: a real Ollama model as the L2 arbiter. Schema-valid rate (constrained JSON) on 50 prompts, accuracy on the router questions,
latency p50/p95. Usage: python scripts/spike_ollama.py <model-name> -> prints one JSON line."""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
from ragnarok.core.errors import ModelError  # noqa: E402
from ragnarok.llm_gateway.client import OllamaGateway  # noqa: E402
from ragnarok.llm_gateway.models import ModelEntry, ModelLock  # noqa: E402
from ragnarok.router.backends.ollama_small import OllamaSmallBackend  # noqa: E402
from ragnarok.router.cascade import Q_FAMILY, Q_INJECT, Q_RISK  # noqa: E402

model = sys.argv[1]
hand = [dict(text=t, family=f, injection=i, risk=r) for t, f, i, r in json.loads(Path("docs/spikes/laya_eval_set.json").read_text(encoding="utf-8"))]
synth = [json.loads(x) for x in Path("evidence/router_synth.jsonl").read_text(encoding="utf-8").splitlines()][:20]
rows = hand + synth
QS = [Q_FAMILY, Q_INJECT, Q_RISK]


async def main():
    gw = OllamaGateway(ModelLock({"arbiter": ModelEntry(model)}), keep_alive="10m")
    be = OllamaSmallBackend(gw)
    await be.decide("warm up", QS, deadline=time.monotonic() + 300)
    valid = 0
    lat = []
    ok = {"family": 0, "injection": 0, "risk": 0}
    for r in rows:
        t0 = time.perf_counter()
        try:
            a = await be.decide(r["text"], QS, deadline=time.monotonic() + 120)
        except ModelError:
            continue
        lat.append((time.perf_counter() - t0) * 1000)
        valid += 1
        ok["family"] += a[0].value == r["family"]
        ok["injection"] += ("yes" if a[1].value else "no") == r["injection"]
        ok["risk"] += a[2].value == r["risk"]
    n = len(rows)
    lat.sort()
    print(json.dumps({"model": model, "n": n, "schema_valid_rate": round(valid / n, 3), "p50_ms": round(statistics.median(lat)), "p95_ms": round(lat[int(0.95 * (len(lat) - 1))]),
                      "accuracy_over_valid": {k: round(v / max(valid, 1), 3) for k, v in ok.items()}}))


asyncio.run(main())
