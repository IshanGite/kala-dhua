# ADR 001: Model plan under a slow network

**Context.** On 25 Oct the build machine measured about 130 KB/s sustained download (Cloudflare 20 MB test and the Ollama registry both agree). A 4 GB model would take over four hours and competes with Python package downloads.

**Choice.**
1. Python packages first; models second.
2. Embeddings use `nomic-embed-text` (274 MB, already pulled).
3. The arbiter and planner use the smallest tool-capable models that fit the link, chosen in P0-03 by measured schema-valid rate. Until a larger planner is pulled, the planner role may point at the same small model (the gateway only needs a name in `models.lock`).
4. The teammate on Windows (better bandwidth, unknown) pulls the larger planner and copies the `~/.ollama/models` folder or reports its digest.
5. All router logic is tested with the fake and cassette backends, so progress does not wait on model downloads.

**Why.** The deadline is 09:00 IST; code and tests matter more than a bigger model, and the cascade is designed so a small model is enough for most decisions.

**Reverse.** Edit `models.lock`, run `ragnarok doctor`. No code changes.
