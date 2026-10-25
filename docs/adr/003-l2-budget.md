# ADR 003: the small arbiter gets its own latency budget

Context: the cascade had one 700 ms budget for all classification. Measured on this Mac (docs/spikes): Laya 4 questions about 170 ms, embedding about 25 ms, but the Ollama arbiter (qwen3:1.7b, constrained JSON) p50 1.28 s, p95 1.37 s. Inside one 700 ms budget the arbiter always timed out and routing silently degraded to "best embedding candidate".
Choice: keep 700 ms for L0/L1 (the interactive fast path). The L2 arbiter gets its own budget (`l2_timeout_ms`, 2500 in the daemon). The embedding fallback remains if it still times out.
Why: honest numbers; first-time requests that Laya abstains on cost up to about 1.5 s once, and then become L0 route hits (no model call). Interactive replies that Laya answers confidently stay near 200 ms.
Reverse: pass `l2_timeout_ms=None` (equal to the interactive budget).
