# Handover for Tushar (Windows)

Updated 2026-10-25 (evening). Read `CLAUDE.md` first: the rules there are binding. Do not add Claude or any co-author trailers to commits.

## What this is
Ragnarok: an always-on personal AI agent that runs entirely on the machine. Local models (Ollama), an encrypted memory with provenance, sandboxed skills, and every write action gated behind a click. Specs: `docs/ARCHITECTURE.md`, `docs/BUILD_PLAN.md`.

## Get running on Windows (about 20 minutes, mostly downloads)
1. Install Python 3.12, `uv`, Git, and Ollama (ollama.com). Optional: Docker Desktop in **Linux containers** mode.
2. `git clone https://github.com/IshanGite/kala-dhua` and `cd kala-dhua`.
3. `uv sync` (add `--extra laya --extra window` for the classifier and the window).
4. `ollama pull nomic-embed-text` and `ollama pull qwen3:1.7b` (about 1.7 GB).
5. `uv run ragnarok doctor` then `uv run ragnarok check`. Both should pass; tell Ishan if not.
6. Edit nothing in `models.lock` unless you change a model (it pins names and digests; digests differ per machine only if the model differs).

## Done (macOS-verified, tests green on macOS and Windows CI)
- Encrypted store (SQLCipher), memory service, provenance, bitemporal facts, hash-chained events, hybrid retrieval, permanent delete with proof, retention and consolidation.
- Zero Trust: policy engine, taint, broker (click-gated approvals), guard, canaries, snapshots, skills registry, sandbox contract + macOS Seatbelt + container backend.
- Executor with crash recovery, router (route library, calibration, Laya and Ollama backends), grounded chat answers.
- Connectors (all read side, tested on recorded fixtures AND live on Ishan's accounts): Slack, Telegram, Discord, Gmail, Google Calendar, Drive, Meet, Outlook (code ready, not yet tried live), local files, desktop notifications (macOS reader). `ragnarok connect <name>` stores tokens in the OS keychain.
- Always-on runtime, local API, always-on-top window (`ragnarok window`), on/off switch, activity feed, sender pairing.
- Measured spikes in `docs/spikes/` (Laya latency and accuracy, calibration, qwen3 arbiter).

## Yours: Windows-specific work, in this order
1. **Run it and report**: `uv run ragnarok run` then in another shell `uv run ragnarok window`. Check the window opens, the switch works, chat answers "I don't have anything about that in memory yet" on an empty store. Note anything that differs from macOS.
2. **Spike 9 on Windows**: `uv run python scripts/spike_laya.py cpu` (and `cuda` if you have an NVIDIA GPU). Save the JSON as `docs/spikes/laya_windows.json`. Laya needs `uv sync --extra laya`; the first load downloads about 1.8 GB.
3. **Sandbox backends** in `src/ragnarok/sandbox/windows.py` (currently stubs that report grade NONE honestly): an AppContainer backend (preferred) and a restricted-token + Job Object fallback. Contract and the shared suite are in `sandbox/contract.py` and `sandbox/suite/`. The six Windows escape tests are listed in `docs/BUILD_PLAN.md` (search "AppContainer"). Until this exists, skills that need a sandbox refuse to run on Windows by design.
4. **Notification reader**: implement `NotificationSource` for Windows in `src/ragnarok/connectors/notifications.py` (see the class docstring). Options: the WinRT `UserNotificationListener` (asks the user for access) or reading `%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db`. Keep every privacy rule: off until apps are named, secrets dropped, content never logged. Replace `UnavailableSource` in `default_source()`.
5. **Autostart**: a Task Scheduler entry that runs `ragnarok window` at login (`ragnarok install` command, not written yet). macOS equivalent is a launchd plist (Ishan).
6. **Windows credential size**: Credential Manager entries are limited to about 2.5 KB; `platform/secrets.py` chunks values. Test with a real Google token JSON.
7. Anything in CI that fails on the Windows runner is yours to fix first (see `.github/workflows/ci.yml`).

## Connect the services on your side (only if you want live data on Windows)
`docs/CONNECTOR_SETUP.md` has every console step. Tokens live in your own Credential Manager; they are never in the repo.

## Rules that bite on Windows
- UTF-8 everywhere (`encoding="utf-8"` on every text open; a test enforces it). Use `pathlib`. No `os.fork`, no `SIGKILL` outside `platform/`.
- Processes use `spawn`. Subprocess calls take argument lists, never shell strings.
- Docker must be in Linux-containers mode; the container backend checks.
- Run `uv run ragnarok check` before every push. Never leave `main` red. Small commits, conventional prefixes (`feat:`, `fix:`, `test:`, `docs:`).

## Known problems / honest gaps
- Outlook connector has not been run against a real account yet.
- Slack is read by polling (no live push). Telegram replies and any sending are not built; all write paths are designed but not exposed.
- The planner role uses the same 1.7B model as the arbiter until a bigger model is measured (`models.lock` is the single switch).
- Laya zero-shot accuracy is modest and calibrated, not fine-tuned. See `docs/spikes/laya.md`.
- The window is a single HTML page (no build step); memory explorer, delete preview and route library screens are not in it yet.
