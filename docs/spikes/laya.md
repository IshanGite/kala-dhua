# Spike 9: Laya on this Mac (Apple silicon, Python 3.12, torch from `uv sync --extra laya`)

Measured 2026-10-25 with `scripts/spike_laya.py` (raw output in `laya_cpu.json`, `laya_mps.json`). HF hub forced offline: the weights load without a network.

| Device | Load (warm cache) | 1 question p50 | 10 questions p50 | Resident memory | Listening ports |
|---|---|---|---|---|---|
| CPU | 6.5 s | 72 ms | 333 ms | 1.8 GB | none |
| MPS | 2.3 s | 35 ms | 256 ms | 0.4 GB | none |

Decision: `laya_backend = laya`, device chosen by `best_device()` (cuda, then mps, then cpu). Both fit the 700 ms interactive budget. No port is opened when used in-process (we never run `laya-serve`).
Windows: no MPS, so CPU (or CUDA if present). Tushar to repeat the timing on the Windows machine and add `laya_windows.json`.

## Zero-shot quality on the router's own questions (`scripts/eval_laya.py`, `laya_eval_set.json`, n = 30, labelled by hand by the author)

| Question | Accuracy |
|---|---|
| skill_family (8 families) | 83.3 % |
| injection (yes/no) | 83.3 %; recall 5/5; 3 false positives of 25 |
| risk_precheck (none/low/high) | 60.0 % |

3 requests (3 questions each): 135 ms per request on MPS.
Only 1 of 30 family answers and 8 of 30 injection answers reach confidence 0.8; the checkpoint warns that its shipped temperatures are out of range and "confidence is uncalibrated".
Consequence for the design: until calibration/fine-tuning, most requests escalate to L2 (as designed), and Laya only raises caution (D-07), which is the safe direction. Risk_precheck at 60 % is not usable alone; policy rules decide risk (D-07), Laya's value is advisory.
Small sample, single labeller: this is a first read, not a benchmark. Next: calibrate on held-out data (temperature scaling in `router/calibration.py`), then the local fine-tune if time allows, and an ablation against Ollama-only.

## Calibration (`scripts/gen_router_data.py`, `scripts/calibrate_laya.py`, output `laya_calibration.json`, table `evidence/laya_calibration.json`)
Fitted per decision on 199 seeded synthetic requests (temperature scaling, threshold chosen for 95 % precision on accepted answers); evaluated on 167 held-out synthetic requests (same templates: optimistic) and the 30 hand-labelled requests (different distribution: the fair check).

| Decision | T | threshold | hand-labelled: precision when accepted | coverage | accuracy over all |
|---|---|---|---|---|---|
| skill_family | 0.6 | 0.678 | 94.1 % | 56.7 % | 83.3 % |
| injection | 0.5 | 0.558 | 93.1 % | 96.7 % | 90.0 % |
| risk_precheck | 0.9 | 0.549 | 100 % (2 accepted) | 6.7 % | 60.0 % |

Reading: calibrated Laya answers a bit over half of family decisions at about 94 % precision and sends the rest up the cascade, which is the intended behaviour. Risk is left to policy rules. All samples are small (n = 30 real); do not quote as a benchmark. A local fine-tune on a larger set is the next lever.

## Local fine-tune (`scripts/finetune_laya.py`, result `docs/evidence/laya_finetune.json`)
Trained on this Mac (Apple GPU, encoder frozen, heads only, 8 epochs, 554 s) on 513 rows: seeded templates plus 64 author-written phrasings. Tested on the 30 author-written requests that are in no training row (small sample, one labeller: not a benchmark).

| Question | Before | After |
|---|---|---|
| skill_family accuracy | 76.7 % | 76.7 % (unchanged) |
| skill_family answers at confidence 0.8 or more | 0 of 30 | 19 of 30, 94.7 % correct |
| injection accuracy | 83.3 % | 90.0 % (recall 100 %, 1 false positive) |

Reading: fine-tuning did not raise raw accuracy on family, but the model is now sure about far more requests (19 of 30 against none) and right 94.7 % of the time when sure, so fewer requests fall through to the slower small model. It is overconfident on injection (29 of 30 answers at 0.8 or more, 89.7 % correct); Laya only adds caution, so that is the safe direction. A full encoder fine-tune was tried first and abandoned: one epoch took about 40 minutes on this machine. The fine-tuned weights stay out of the repository; use them with `RAGNAROK_LAYA_CKPT=models-local/laya-ft/ckpt`. Not on by default until the cascade thresholds are re-fitted on it.
