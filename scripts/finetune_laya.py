"""Fine-tune Laya locally on the router's own questions (skill family, injection) and measure it on requests it never saw.

Train: evidence/router_synth.jsonl (seeded templates) + docs/spikes/laya_hand_train.json (author-written phrasings).
Test:  docs/spikes/laya_eval_set.json (30 author-written requests, none in training). Output: models-local/laya-ft/ (git-ignored) and docs/evidence/laya_finetune.json.
Run: uv run python scripts/finetune_laya.py [epochs]
"""
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("USE_TF", "0")
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, "src")
import laya  # noqa: E402
from laya.train import TrainConfig, finetune  # noqa: E402

from ragnarok.router.backends.laya import best_device, from_laya, to_laya_spec  # noqa: E402
from ragnarok.router.cascade import Q_FAMILY, Q_INJECT  # noqa: E402

EPOCHS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
QS = [Q_FAMILY, Q_INJECT]
SPEC = to_laya_spec(QS)
OUT = Path("models-local/laya-ft")
BASE = next(Path.home().glob(".cache/huggingface/hub/models--convaiinnovations--laya/snapshots/*/typed-decisions"))
test = json.loads(Path("docs/spikes/laya_eval_set.json").read_text(encoding="utf-8"))
test_texts = {t[0].lower() for t in test}


def row(text, fam, inj):
    return {"state": text, "questions": SPEC, "expected": {"skill_family": fam, "injection": inj == "yes"}}


train = []
for line in Path("evidence/router_synth.jsonl").read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    if r["text"].lower() not in test_texts:
        train.append(row(r["text"], r["family"], r["injection"]))
train += [row(*h) for h in json.loads(Path("docs/spikes/laya_hand_train.json").read_text(encoding="utf-8"))]
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "train.jsonl").write_text("\n".join(json.dumps(r) for r in train) + "\n", encoding="utf-8")
(OUT / "test.jsonl").write_text("\n".join(json.dumps(row(t[0], t[1], t[2])) for t in test) + "\n", encoding="utf-8")


def evaluate(model_dir, device):
    agent = laya.load(str(model_dir), device=device)
    res = {"n": len(test)}
    rows = [(t, from_laya(agent.predict(t[0], SPEC), QS)) for t in test]
    for qi, (name, idx) in enumerate([("skill_family", 1), ("injection", 2)]):
        ok = conf = conf_ok = 0
        for t, a in rows:
            pred = a[qi].value if name != "injection" else ("yes" if a[qi].value else "no")
            good = str(pred) == t[idx]
            ok += good
            if a[qi].probability >= 0.8:
                conf += 1
                conf_ok += good
        res[name] = {"accuracy": round(ok / len(rows), 3), "answers_ge_0.8_conf": conf, "accuracy_when_ge_0.8": round(conf_ok / conf, 3) if conf else None}
    inj = [r for r in rows if r[0][2] == "yes"]
    res["injection_recall"] = round(sum(1 for t, a in inj if a[1].value) / len(inj), 3)
    res["injection_false_positives"] = sum(1 for t, a in rows if t[2] == "no" and a[1].value)
    return res


dev = best_device()
before = evaluate("convaiinnovations/laya" if False else BASE, dev) if False else None
agent_before = laya.load("convaiinnovations/laya", subfolder="typed-decisions", device=dev)
rows = [(t, from_laya(agent_before.predict(t[0], SPEC), QS)) for t in test]
before = {"n": len(test)}
for qi, (name, idx) in enumerate([("skill_family", 1), ("injection", 2)]):
    ok = conf = conf_ok = 0
    for t, a in rows:
        pred = a[qi].value if name != "injection" else ("yes" if a[qi].value else "no")
        good = str(pred) == t[idx]
        ok += good
        if a[qi].probability >= 0.8:
            conf += 1
            conf_ok += good
    before[name] = {"accuracy": round(ok / len(rows), 3), "answers_ge_0.8_conf": conf, "accuracy_when_ge_0.8": round(conf_ok / conf, 3) if conf else None}
del agent_before
print("before:", json.dumps(before), flush=True)
t0 = time.time()
summary = finetune(str(OUT / "train.jsonl"), str(BASE), str(OUT / "ckpt"), TrainConfig(epochs=EPOCHS, micro_batch=8, grad_accum=2, loss="soft-ce", seed=7), device=dev)
secs = round(time.time() - t0)
after = evaluate(OUT / "ckpt", dev)
print("after:", json.dumps(after), "train_seconds", secs, flush=True)
Path("docs/evidence").mkdir(parents=True, exist_ok=True)
Path("docs/evidence/laya_finetune.json").write_text(json.dumps({"train_rows": len(train), "epochs": EPOCHS, "device": dev, "train_seconds": secs, "test": "30 author-written requests, none in training", "before": before, "after": after}, indent=1), encoding="utf-8")
