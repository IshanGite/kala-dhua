"""Fit per-decision temperature + threshold for Laya on the synthetic set (half), report on the other half AND on the 30 hand-labelled requests
(a different distribution: the honest check). Writes docs/spikes/laya_calibration.json and evidence/laya_calibration.json (the fitted table)."""
import json
import os
import sys
import time
import zlib
from pathlib import Path

os.environ.setdefault("USE_TF", "0")
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, "src")
import laya  # noqa: E402

from ragnarok.router.backends.laya import best_device, from_laya, to_laya_spec  # noqa: E402
from ragnarok.router.calibration import Calibrator, fit_temperature, fit_threshold  # noqa: E402
from ragnarok.router.cascade import Q_FAMILY, Q_INJECT, Q_RISK  # noqa: E402

QS = [("skill_family", Q_FAMILY, "family"), ("injection", Q_INJECT, "injection"), ("risk_precheck", Q_RISK, "risk")]
spec = to_laya_spec([q for _, q, _ in QS])
agent = laya.load("convaiinnovations/laya", subfolder="typed-decisions", device=best_device())
synth = [json.loads(line) for line in Path("evidence/router_synth.jsonl").read_text(encoding="utf-8").splitlines()]
hand = [dict(text=t, family=f, injection=i, risk=r) for t, f, i, r in json.loads(Path("docs/spikes/laya_eval_set.json").read_text(encoding="utf-8"))]


def run(rows):
    out = []
    for r in rows:
        ans = from_laya(agent.predict(r["text"], spec), [q for _, q, _ in QS])
        out.append((r, ans))
    return out


def truth(name, r):
    return {"injection": "true" if r["injection"] == "yes" else "false"}.get(name, r["family"] if name == "skill_family" else r["risk"])


def label(name, a):
    return ("true" if a.value else "false") if name == "injection" else str(a.value)


t0 = time.time()
s_all, h_all = run(synth), run(hand)
cal_half = [x for x in s_all if zlib.crc32(x[0]["text"].encode()) % 2 == 0]
test_half = [x for x in s_all if zlib.crc32(x[0]["text"].encode()) % 2 == 1]
cal = Calibrator()
report = {"device": best_device(), "n_synth": len(synth), "n_calibration": len(cal_half), "n_heldout_synth": len(test_half), "n_hand": len(hand), "seconds": round(time.time() - t0)}
for qi, (name, _, _) in enumerate(QS):
    samples = [(a[qi].distribution, truth(name, r)) for r, a in cal_half if a[qi].distribution]
    T = fit_temperature(samples)
    cal.set(name, "laya", T, 1.01)
    conf = [(cal.adjust(name, "laya", a[qi]).probability, label(name, a[qi]) == truth(name, r)) for r, a in cal_half]
    th = fit_threshold(conf, target_precision=0.95)
    cal.set(name, "laya", T, th)

    def evaluate(rows):
        acc = [(label(name, a[qi]) == truth(name, r), cal.accepts(name, "laya", a[qi])) for r, a in rows]
        taken = [ok for ok, acc_ in acc if acc_]
        return {"accuracy_all": round(sum(ok for ok, _ in acc) / len(acc), 3), "coverage": round(len(taken) / len(acc), 3), "precision_when_accepted": round(sum(taken) / len(taken), 3) if taken else None}

    report[name] = {"temperature": T, "threshold": round(th, 3), "heldout_synth": evaluate(test_half), "hand_labelled": evaluate(h_all)}
Path("docs/spikes/laya_calibration.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
Path("evidence/laya_calibration.json").write_text(json.dumps({f"{k[0]}/{k[1]}": {"temperature": v.temperature, "threshold": v.threshold} for k, v in cal.table.items()}, indent=1), encoding="utf-8")
print(json.dumps(report))
