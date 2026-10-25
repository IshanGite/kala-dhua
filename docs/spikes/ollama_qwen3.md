# Spike 2: Ollama arbiter (qwen3:1.7b), measured 2026-10-25 on this Mac (`scripts/spike_ollama.py`, raw `ollama_qwen3-1.7b.json`)

50 prompts (30 hand-labelled + 20 synthetic), the router's three questions per prompt, constrained JSON (`format` = JSON schema), temperature 0.

| Measure | Result | Gate |
|---|---|---|
| Schema-valid rate | 100 % (50/50) | >= 95 % |
| Latency p50 / p95 | 1282 ms / 1367 ms | (L2 is allowed to be slower than Laya) |
| Accuracy: skill family / injection / risk | 80 % / 88 % / 48 % | informational |

Finding that mattered: qwen3 "thinks" by default; with a 200-token cap the reasoning consumed the whole budget and `content` was empty (invalid JSON on the very first call). The gateway now sends `"think": false` on every call. Without it this model is unusable for constrained output.
Risk precheck at 48 % again confirms: risk is decided by policy rules, never by a model (D-07).
Planner role currently points at the same 1.7B model; qwen3:4b is being pulled, to be measured and swapped in if it is better (models.lock is the single switch).
