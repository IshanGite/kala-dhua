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
