# Handover for Tushar (Windows)

Updated 2026-10-25 night. Read `CLAUDE.md` first: the rules there are binding. Do not add Claude or any co-author trailers to commits. Also read `docs/REQUIREMENTS_TRACE.md`: it maps every requirement of the original problem statement to code, tests and evidence, and lists the gaps honestly.

## What this is
Ragnarok: an always-on personal AI agent that runs entirely on the machine. Local models (Ollama plus Laya), an encrypted memory with provenance, sandboxed skills, and every write action gated behind one click. Specs: `docs/ARCHITECTURE.md`, `docs/BUILD_PLAN.md`.

## Get running on Windows (about 20 minutes, mostly downloads)
1. Install Python 3.12, `uv`, Git, and Ollama (ollama.com). Optional: Docker Desktop in **Linux containers** mode.
2. `git clone https://github.com/IshanGite/kala-dhua` and `cd kala-dhua`.
3. `uv sync --extra laya --extra window`.
4. `ollama pull nomic-embed-text`, `ollama pull qwen3:0.6b`, `ollama pull qwen3:1.7b`, `ollama pull qwen3:4b` (about 4.7 GB in total; the 4B is only for planning and catch-up briefings), and `ollama pull qwen3-vl:2b` (1.9 GB, for explain-my-screen).
5. `uv run ragnarok doctor` then `uv run ragnarok check`. Both should pass; tell Ishan if not.
6. `models.lock` pins names and digests; a digest that differs on your machine is refused on purpose. Re-pin only if you change a model.

## What is done (macOS-verified; core tests also green on Windows CI)
- **Memory**: encrypted store, provenance, bitemporal facts, hash-chained events, hybrid retrieval, remember/recall by command, episodic history, daily consolidation and forgetting, permanent delete with a verification scan, export.
- **Safety**: policy engine, taint tracking, click-gated approvals, guard, canaries, snapshots, skill registry with install scan and hash pinning, macOS Seatbelt sandbox (13/13 attacks contained), container backend, typed "yes" can never approve.
- **Brain**: four-tier router (stored routes, command rules, Laya, embedding match, small arbiter, planner), route learning, deterministic multi-step decomposition, retries, one self-correcting re-plan, safe calculator, grounded answers with citations and a "Why?" trace.
- **Always-on**: continuous ingestion, user-defined triggers, heads-ups, needs-attention flags, daily briefing, inbox, OS notifications (macOS), boot recovery (state back in 3.3 s after kill -9).
- **Connectors**: Gmail, Calendar, Drive, Meet, Slack, Telegram, Discord (read); email send/reply/draft, calendar events, Drive documents, Telegram, Discord, Slack (writes, click-gated); Outlook coded, not yet run live.
- **Window**: chat (suggestions, Explain my screen, picture questions), Digest (contexts, timeline), Inbox, Approvals, Memory (search, delete with preview and receipt, edit and export), Activity, Sources (per-app health, notification permission guide, briefing time, skills).
- **Live ingestion**: one polling task per source (`daemon/runtime.py`, `DEFAULT_INTERVALS`), Telegram long poll, backoff. Test scripts must set `RAGNAROK_SKIP_SOURCES=telegram` so they never consume real Telegram updates.
- **Understanding**: `harness/understand.py` (model decides the path; rules + fallbacks remain when the model is down). QA harnesses: `scripts/qa_chat.py`, `scripts/qa_edge.py` (real models, fake senders, nothing real is sent), `scripts/e2e_full.py` (real accounts; run with `RAGNAROK_SKIP_SOURCES=telegram`).
- **Demo mode**: `ragnarok window --demo` (`daemon/demo.py`): synthetic data for every app, fake senders, outbox, live incoming feed. Safe: no credentials.
- **Screen**: `platform/screen.py` (modes screen/region/window, displays), `platform/ocr.py` (macOS Vision framework via a cached Swift binary; **Windows OCR and capture are yours**: Windows.Media.Ocr + Windows.Graphics.Capture), `harness/vision.py` (Smart/Read/Deep).
- **If `ragnarok` says `No module named 'ragnarok'` on macOS**: the editable-install .pth files get marked hidden and Python skips them; a `sitecustomize.py` in the venv works around it (see git history), or run `uv pip install -e .` again.
- **Voice**: `platform/voice.py` (Whisper base offline via transformers; microphone recording through a cached Swift tool on macOS: **Windows microphone recording is yours**, the Whisper part is portable and only needs a 16 kHz mono WAV). ADR 004.
- **Window shell**: `daemon/shell.py` (pin to an edge, snap to half, desktops) and `ui/mascot.html` (the raven). pywebview move/hide/show work on Windows; the macOS-only parts are `_mac_all_desktops` and `_mac_next_desktop` (Windows: virtual desktops need the VirtualDesktop COM API). Tests: `tests/unit/test_shell.py`, `node tests/ui/mascot.mjs`.
- **Understanding**: `harness/actionparse.py` (who/what/when from any wording), `harness/understand.py` (intent). `scripts/qa_nl.py` is the multilingual set.
- **Vision**: `harness/vision.py`, `platform/screen.py` (macOS `screencapture`; **Windows capture is yours**: implement `capture_screen()` for Windows and return PNG bytes), model `qwen3-vl:2b` (`ollama pull qwen3-vl:2b`).
- **Tests**: 360+ unit and security tests, plus `node tests/ui/smoke.mjs` (16 browser-level checks; run `npm install` once in `tests/ui`).

## Yours: Windows-specific work, in this order
1. **Run it and report**: `uv run ragnarok run`, then in another shell `uv run ragnarok window`. Check the window opens, the switch works, the Inbox and Messages tabs render, chat answers "I don't have anything about that in memory yet" on an empty store. Note anything that differs from macOS.
2. **Spike 9 on Windows**: `uv run python scripts/spike_laya.py cpu` (and `cuda` with an NVIDIA GPU). Save the JSON as `docs/spikes/laya_windows.json`. Then `uv run python scripts/bench_nfr2_1.py` and `uv run python scripts/bench_restart.py` and commit the JSON they write to `docs/evidence/` renamed with a `_windows` suffix.
3. **Sandbox backends** in `src/ragnarok/sandbox/windows.py` (currently stubs that report grade NONE honestly): an AppContainer backend (preferred) and a restricted-token + Job Object fallback. Contract and shared suite: `sandbox/contract.py`, `sandbox/suite/`. Until this exists, skills that need a sandbox (python.run, fs.search) refuse to run on Windows, by design.
4. **Notification reader**: implement `NotificationSource` for Windows in `src/ragnarok/connectors/notifications.py` (WinRT `UserNotificationListener`, or `%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db`). Keep every privacy rule: off until apps are named, secrets dropped, content never logged. Also `src/ragnarok/platform/notify.py` has a macOS-only `notify()`; add a Windows toast.
5. **Autostart**: a Task Scheduler entry that runs `ragnarok window` at login (there is no `ragnarok install` command yet).
6. **Windows credential size**: Credential Manager entries are limited to about 2.5 KB; `platform/secrets.py` chunks values. Test with a real Google token JSON.
7. Anything in CI that fails on the Windows runner is yours to fix first (`.github/workflows/ci.yml`).

## Ideas that would make the pitch stronger (pick any, each is self-contained)
- A timeline screen for the episodic log (`events` table) next to Messages.
- A "Route Library" screen showing learned routes and their success counts (`routes` and `route_events` tables); the learning curve plot from `tests/unit/test_ROUTE_learning.py` data.
- File-change triggers as a rule ("when a file appears in ~/Downloads, tell me") on top of the local-files connector.
- Model-based fact extraction from ingested text behind a switch (today facts come from explicit commands and rules).
- Laya fine-tune from the seeded data in `evidence/router_synth.jsonl` (calibration is done; fine-tuning would raise its confident coverage above 30 percent).

## Rules that bite on Windows
- UTF-8 everywhere (`encoding="utf-8"` on every text open; a test enforces it). Use `pathlib`. No `os.fork`, no `SIGKILL` outside `platform/`.
- Processes use `spawn`. Subprocess calls take argument lists, never shell strings.
- Docker must be in Linux-containers mode; the container backend checks.
- Run `uv run ragnarok check` before every push. Never leave `main` red. Small commits, conventional prefixes. Pull with `git pull --rebase` (Ishan and you both push to `main`).

## Known problems / honest gaps
- Outlook and desktop notifications have not been run on real accounts.
- Laya is calibrated, not fine-tuned (see `docs/spikes/laya.md`).
- The LLM planner fallback takes 20 to 30 s and can invent skills; common multi-step shapes use the code decomposer instead.
- The red-team report (`docs/RED_TEAM.md`) and a one-command acceptance runner are not written; `tests/security/` and the live injection test are the evidence for now.
- Sandbox, notification reader and autostart on Windows are open (above).
