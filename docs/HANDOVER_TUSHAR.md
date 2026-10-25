# Handover for Tushar (Windows)

Last updated: 25 Oct 2026, 13:05 IST. This file is rewritten at every milestone and once more at wrap-up.

## What this repo is
Ragnarok / kala-dhua: a sovereign always-on personal AI agent. Read `docs/ARCHITECTURE.md` (locked decisions D-01..D-30) and `docs/BUILD_PLAN.md` (phases and gates) first. `CLAUDE.md` holds the working rules.

## Get running on Windows (about 15 minutes, mostly downloads)
1. `winget install astral-sh.uv Git.Git Ollama.Ollama` (check ids with `winget search`).
2. `git clone https://github.com/IshanGite/kala-dhua.git` then `cd kala-dhua`.
3. `uv sync` (Python 3.12 is installed by uv).
4. `uv run ragnarok doctor` and paste the output into `docs/spikes/windows-doctor.md`.
5. Pull models: `ollama pull nomic-embed-text`, then the planner and the arbiter (see "Models" below).

## Done so far
- Repo scaffold, pinned stack, CI (macOS + Windows tests, Ubuntu lint), hygiene checks.
- Core utilities: Clock, ids, errors, failpoints, content-free logging.
- Encrypted store: key hierarchy, SQLCipher connection with authorizer, numbered migrations covering all 11 memory types and the runtime/security/routing tables.
- LLM Gateway: loopback-only Ollama client with a model allowlist, fake and cassette backends.
- Local API skeleton: loopback bind, session token, Host/Origin checks, rate-limited failures.
- `ragnarok doctor` (python, Ollama, lockdown, encrypted store self-test, secret store, hygiene, profile).

## Next (in this order)
See the phase table in `docs/BUILD_PLAN.md`. The next blocks are P2 memory service + retrieval, P3 ingestion + connectors, P4 skills/sandbox/broker.

## Windows-specific work that is yours
- **Spike:** does `sqlcipher3-wheels` install and pass `uv run pytest tests/unit/test_NFR1_2_encrypted_store.py` on Windows? Record in `docs/spikes/01-sqlcipher.md`. Note: `sqlcipher3-binary` has no macOS wheel; the wheels fork is what we use.
- **Sandbox backends:** `src/ragnarok/sandbox/appcontainer.py` and `restricted.py` (Job Object + restricted token), plus the six Windows escape tests in the BUILD_PLAN. Container backend (Docker/Podman) is the common fallback.
- **Platform package:** Windows implementations for autostart (Task Scheduler), process control (Job Objects), thermal proxy, GPU metering (`typeperf`), notifications.
- **Models:** the Mac has very slow internet (about 130 KB/s). If your connection is faster, pull the larger planner model and report the exact name and digest so we can lock it in `models.lock`.

## Rules that bite on Windows
UTF-8 everywhere (`PYTHONUTF8=1`), `pathlib` only, no `os.fork`/`SIGKILL`, `spawn` start method, no reserved file names. `uv run pytest tests/unit/test_NFR1_3_local_inference.py` enforces most of these.

## Known problems
- Download bandwidth on the Mac is the main constraint (ADR 001).
- Nothing has been run on Windows yet.
