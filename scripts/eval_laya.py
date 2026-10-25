"""Zero-shot accuracy of Laya on the router's own questions, against a hand-labelled set (docs/spikes/laya_eval_set.json).
Prints accuracy per question, accuracy of confident answers, and the confidence-vs-correctness table. Honest small-sample numbers, not a benchmark."""
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("USE_TF", "0")
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, "src")
import laya  # noqa: E402

from ragnarok.router.backends.laya import best_device, from_laya, to_laya_spec  # noqa: E402
from ragnarok.router.cascade import Q_FAMILY, Q_INJECT, Q_RISK  # noqa: E402

data = json.loads(Path("docs/spikes/laya_eval_set.json").read_text(encoding="utf-8"))
agent = laya.load("convaiinnovations/laya", subfolder="typed-decisions", device=best_device())
qs = [Q_FAMILY, Q_INJECT, Q_RISK]
spec = to_laya_spec(qs)
rows = []
t0 = time.perf_counter()
for text, fam, inj, risk in data:
    ans = from_laya(agent.predict(text, spec), qs)
    rows.append((text, fam, inj, risk, ans))
ms = (time.perf_counter() - t0) / len(data) * 1000
out = {"n": len(data), "ms_per_request_3_questions": round(ms, 1), "device": best_device()}
for qi, (name, idx) in enumerate([("skill_family", 1), ("injection", 2), ("risk_precheck", 3)]):
    ok = conf_ok = conf_n = 0
    table = []
    for r in rows:
        a = r[4][qi]
        pred = a.value if name != "injection" else ("yes" if a.value else "no")
        good = str(pred) == r[idx]
        ok += good
        table.append((round(a.probability, 2), good))
    for c, g in table:
        if c >= 0.8:
            conf_n += 1
            conf_ok += g
    out[name] = {"accuracy": round(ok / len(rows), 3), "answers_ge_0.8_conf": conf_n, "accuracy_when_ge_0.8": round(conf_ok / conf_n, 3) if conf_n else None}
inj_rows = [r for r in rows if r[2] == "yes"]
out["injection_recall"] = round(sum(1 for r in inj_rows if r[4][1].value) / len(inj_rows), 3)
out["injection_false_positives"] = sum(1 for r in rows if r[2] == "no" and r[4][1].value)
print(json.dumps(out))
