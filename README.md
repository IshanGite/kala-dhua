# Ragnarok

**A sovereign, always-on personal AI agent that runs entirely on your own machine.**

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="Local-first AI" src="https://img.shields.io/badge/AI-100%25%20local-2ea44f">
  <img alt="Zero Trust" src="https://img.shields.io/badge/security-zero%20trust-8b5cf6">
  <img alt="Encrypted storage" src="https://img.shields.io/badge/storage-SQLCipher-orange">
  <img alt="Platforms" src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey">
  <img alt="Tests" src="https://img.shields.io/badge/tests-427%20automated-2ea44f">
  <img alt="Status" src="https://img.shields.io/badge/status-under%20active%20construction-yellow">
</p>

Ragnarok reads your **mail, calendar, files and chats**, remembers them **with provenance**, plans and executes multi-step tasks through **sandboxed skills** — and never sends, deletes or changes anything outside its workspace **without a click bound to the exact content**.

> **Local models. Encrypted memory. Zero Trust. Every write gated behind a click.**

---

## Contents

- [Why Ragnarok?](#why-ragnarok)
- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Quickstart](#quickstart)
- [Command-line interface](#command-line-interface)
- [Connectors](#connectors)
- [Models](#models)
- [Skills, skill scrolls & dreaming](#skills-skill-scrolls--dreaming)
- [Voice, vision & screen](#voice-vision--screen)
- [Security & privacy](#security--privacy)
- [Evaluation & evidence](#evaluation--evidence)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [Status](#status)

---

## Why Ragnarok?

Five principles guide every feature:

| Pillar | What it means |
|---|---|
| **Sovereignty you can prove** | One data directory, loopback-only services, an egress allowlist, packet-capture evidence, and an offline demo. |
| **Memory with provenance** | Eleven memory types in a single encrypted file; every fact links to a quote in a source; delete cascades and is verified by a scan. |
| **Safe autonomy** | Zero Trust: the model is assumed already tricked. Code-enforced policy, sandboxing, taint tracking, and content-bound approvals. |
| **Fast by design** | A confidence-gated cascade; most decisions never reach a generative model, and the cascade learns from its own successes. |
| **Evidence, not claims** | Every number comes from this project's own harness, per machine, merged into `docs/evidence/` and `docs/EVALS.md`. |

---

## What it does

- **Ingests** Gmail, Google Calendar, Drive, Meet, Slack, Telegram, Discord, Outlook, local files, and desktop notifications — each source on its own live poller.
- **Remembers** everything in an encrypted SQLCipher store with provenance, bitemporal facts, and hash-chained events.
- **Routes** every request through a four-tier cascade (learned routes → Laya → small model → big model), so repeat tasks get faster.
- **Plans and executes** multi-step tasks: *"look up what Dev needs and remind me about it tomorrow"* is split into an execution graph by code in about 0.1 s (the LLM planner is only the fallback), with crash recovery, retries and one self-correcting re-plan.
- **Works on its own, within limits**: user-defined triggers (*"every day at 8am catch me up"*, *"when I get an email from Dev, tell me"*), heads-ups before meetings, "needs your attention" flags, a daily briefing, and an inbox — autonomous runs are never trusted more than typed ones.
- **Distils your day into a Digest**: a context-grouped summary across apps with a timeline, faithful citations, and grouped notifications.
- **Sees and hears**: explain your screen or ask about an image with a local vision model; speak in ~99 languages with a local Whisper model.
- **You control the memory**: search, edit, export (JSON or Markdown with provenance) and permanently delete (preview, confirmation token, verified receipt) from the window; every reply has a **Why?** panel.
- **Skills**: create, register and run your own sandboxed skills (`ragnarok skill new/add/list`), plus Markdown **skill scrolls** that draft and self-improve.
- **Guards every write** behind an approval card bound to the exact content — sends, deletes and edits happen only when you click.
- **Serves a local UI** (always-on-top window) plus a CLI for everything, and a **demo mode** that runs the whole app on synthetic data.

---

## How it works

### The four-tier router

| Tier | Component | Handles |
|---|---|---|
| **L0** | Route library | Previously-verified routes matched by embedding similarity. No model call. |
| **L1** | Laya (local classifier) | Skill family, injection and risk pre-checks in one forward pass. |
| **L2** | Small Ollama arbiter | Interprets ambiguous requests and picks skills. |
| **L3** | Big Ollama planner | Plans genuinely complex or multi-step work. |

When a task succeeds, the route that worked becomes a **candidate**, is verified in **shadow mode**, and is promoted into **L0** — so the same kind of request is served faster next time. This is the self-improving loop.

### Zero Trust

- The model is treated as **already manipulated**.
- Every non-human actor has an identity; skills are **hash-verified** at execution time.
- **Taint tracking**: untrusted content can never trigger a sensitive action on its own.
- Egress allowlist, gateway scanning, canaries, budgets, and step-up approval for critical actions.

```mermaid
flowchart LR
  SRC[Sources: Gmail · Calendar · Drive · Meet · Slack · Telegram · Discord · Outlook · files] --> CONN[Connectors]
  CONN --> ING[Ingestion pipeline]
  ING --> MEM[(SQLCipher memory)]
  MEM --> RET[Hybrid retrieval]
  REQ[Request] --> ROUTE{Router<br/>L0 → L1 → L2 → L3}
  RET --> ROUTE
  ROUTE --> HAR[Planner + executor]
  HAR --> POL[Policy engine]
  POL --> BRK[Broker: approvals]
  BRK --> SK[Sandboxed skills]
  ROUTE --> GW[LLM gateway]
  GW --> OLL[Ollama · 127.0.0.1]
  UI[Local UI / CLI] <--> BRK
```

---

## Quickstart

### macOS

```bash
brew install uv git
# install the Ollama app from ollama.com

git clone https://github.com/IshanGite/kala-dhua.git
cd kala-dhua
uv sync --extra laya --extra window
ollama pull nomic-embed-text
ollama pull qwen3:0.6b
ollama pull qwen3:1.7b
ollama pull qwen3:4b
ollama pull qwen3-vl:2b
uv run ragnarok doctor
```

### Windows

```powershell
winget install astral-sh.uv Git.Git Ollama.Ollama

git clone https://github.com/IshanGite/kala-dhua.git
cd kala-dhua
uv sync --extra laya --extra window
ollama pull nomic-embed-text
ollama pull qwen3:0.6b
ollama pull qwen3:1.7b
ollama pull qwen3:4b
ollama pull qwen3-vl:2b
uv run ragnarok doctor
```

> The local voice model (`openai/whisper-base`, ~290 MB) downloads **once, with your approval, on first use**, then transcribes offline.

`uv run ragnarok doctor` is the **sync tool**: run it after every pull, on every machine. `uv run ragnarok check` is the pre-push gate.

### Run it

```bash
uv run ragnarok run       # always-on agent + local API (no window)
uv run ragnarok window    # always-on-top chat/activity window with an on/off switch
uv run ragnarok window --demo   # throwaway copy with synthetic data for every app
```

On an empty store the agent answers *"I don't have anything about that in memory yet"*.

---

## Command-line interface

| Command | Purpose |
|---|---|
| `ragnarok doctor [--benchmark] [--restore-test]` | Check this machine. Run after every pull. |
| `ragnarok check` | Lint + fast tests (the pre-push gate). |
| `ragnarok run [--port] [--interval] [--folder] [--demo]` | Start the always-on agent + local API. |
| `ragnarok window [--port] [--no-top] [--demo]` | Start the always-on-top window. |
| `ragnarok evals [--suite …] [--report-only]` | Run the benchmark suites against the real local models and write `docs/EVALS.md`. |
| `ragnarok connect slack\|telegram\|discord` | Store a bot token in the OS keychain. |
| `ragnarok connect google --client-file <json> [--write]` | Google OAuth sign-in (Gmail, Calendar, Drive, Meet). |
| `ragnarok connect outlook --client-id <id>` | Outlook device-code sign-in. |
| `ragnarok skill new <name>` / `add <folder>` / `list` | Create, register and list your own skills (turn them on in the window). |
| `ragnarok version` | Print the version. |

---

## Connectors

Read and write tokens are **separate**; tokens live in the OS keychain, never in a file. Read comes first, write is a second opt-in.

| Source | Read | Write (click-gated) |
|---|---|---|
| Gmail | ✅ | Draft, reply in thread, send |
| Google Calendar | ✅ | Create event |
| Google Drive | ✅ | Create document |
| Google Meet | ✅ (transcripts) | — |
| Slack | ✅ (polling) | Post in a channel (needs the `chat:write` scope) |
| Telegram | ✅ (long-poll) | Send message |
| Discord | ✅ | Post in channel |
| Outlook | ✅ (coded, not yet live-tested) | Draft & send (designed) |
| Local files | ✅ | — |
| Desktop notifications | Built, not yet live-tested | — |

See **[`docs/CONNECTOR_SETUP.md`](docs/CONNECTOR_SETUP.md)** for the exact console steps for every source.

---

## Models

All generation and embeddings go only to Ollama on `127.0.0.1:11434`, plus Laya in-process. Names and digests are pinned in [`models.lock`](models.lock); the daemon refuses a model whose digest differs.

| Role | Model |
|---|---|
| Embeddings | `nomic-embed-text` |
| Writer (chat & drafts) | `qwen3:0.6b` |
| Arbiter (L2) | `qwen3:1.7b` |
| Planner (L3) | `qwen3:4b` |
| Vision (screen & images) | `qwen3-vl:2b` |
| Voice (speech-to-text) | `openai/whisper-base` (local, offline) |
| Classifier (L1) | Laya (in-process; optional local fine-tune checkpoint) |

No cloud AI service, SDK, key or endpoint is used at any point.

---

## Skills, skill scrolls & dreaming

**Skills** are small programs the agent may call, run in a sandbox and hash-verified at execution time.

- **Built-in**: `fs.read`, `fs.search`, `fs.write`, `note.add`, `python.run`.
- **Native**: `calendar.add`, `calendar.agenda`, `chat.reply`, `drive.create`, `mail.draft`, `mail.reply`, `mail.send`, `memory.search`, `reminder.add`, `reminder.list`.
- **Your own**: `ragnarok skill new` / `add` / `list`, enabled with a step-up approval in the window.

**Skill scrolls** are short Markdown specialists (`src/ragnarok/scrolls/builtin/`): only their headers stay in memory and the full text loads for the one that fits. Eight ship with the app — `meeting-notes`, `email-triage`, `reply-drafter`, `triage-and-reply` (a chain), `explain-simply`, `action-items`, `decision-helper`, `standup-writer`. A scroll is **read-only** (its tools are lookups performed by code) and untrusted until you approve its exact text.

**Dreaming**: when the laptop is plugged in and idle, the agent replays the day, tidies memory, rehearses what failed, and drafts improvements to its own scrolls — all waiting for your approval, never acting outside the computer.

See **[`docs/SKILL_SCROLLS.md`](docs/SKILL_SCROLLS.md)**.

---

## Voice, vision & screen

- **Voice**: the microphone button records, and a local Whisper model transcribes it offline in about 99 languages with automatic detection; free-form requests in other languages and with typos are understood.
- **Screen & images**: capture the whole screen, a region, or a window on any display and run **Smart**, **Read-text**, or **Deep** engines (OS text reader + a language model, with the vision model as fallback). A 3-second countdown, one capture, never stored; text inside an image is treated as data, never instructions.
- **The bolt**: drag the window to a screen edge to pin it — the window hides and a small teal lightning bolt (**Muninn**) waits there, dims when idle and flares with a speech bubble for approvals and reminders until you click it.

---

## Security & privacy

- **Local first** — no cloud AI, no telemetry, egress allowlisted and auditable.
- **Encrypted at rest** — SQLCipher, per-file AES-GCM blobs, secrets only in the OS vault.
- **Zero Trust** — identities, hash-verified skills, taint tracking, gateway scanning, canaries, budgets, step-up approval.
- **Reversible** — deletes go to trash, snapshots before bulk changes, an undo window on every write.
- **Red team** — the security requirements **SEC-01 … SEC-20** are enforced and tested across `tests/security/` and `tests/unit/`, and the benchmark harness includes an injection suite with a second opinion.

---

## The window

A Norse-themed desktop window (Odin's gold, Thor's lightning blue, a glass orb that hovers when pinned). Tabs: Chat (with section bubbles for one-click tasks and a composer that sends to any connected app), Digest, Inbox, Approvals, Memory, Activity, **Graph** (a 3D knowledge graph of memory realms, apps, people, topics, facts, skills, scrolls and models; an optional, clearly marked demo constellation adds extra nodes), Scrolls and Sources.

## Evaluation & evidence

Every claim is measured on this project's own harness and recorded in [`docs/evidence/`](docs/evidence/) and [`docs/EVALS.md`](docs/EVALS.md). The `evals/` harness (`ragnarok evals`) runs ten suites — exact answers, intent, actions, end-to-end tasks, grounded memory, digest, forgetting, security, multimodal and reliability — against the real local models.

**Latest measured run (all models local):**

| Suite | Result |
|---|---|
| Exact answers (time, dates, units) | 93/93 |
| Understanding intent | 145/156 correct; 0/122 non-actions treated as actions; 3/34 actions missed |
| Reading action requests | 37/37 right skill; 36/37 fully right (the one miss read "in an hour" as 30 minutes) |
| Doing tasks end to end | 37/37 succeeded; 0 wrong or extra side effects; 0/16 denied-but-executed |
| Grounded memory answers | 23/24 correct; 0/10 invented answers |
| Digest | 0/90 wrong merges; only 5/15 same-topic pairs grouped (deliberately conservative) |
| Forgetting (180 simulated days) | 180/180 frequently used facts kept; 5/5 pinned kept; unused facts pruned (127/720 still active) |
| Security | 0 of 74 attacks succeeded (95% CI up to 4.9%); held-out injections: scanner alone 2/12, with the small-model second opinion 12/12, 0/30 false alarms |
| Multimodal | OCR 47/47 words; vision engines 8/8 each; speech language ID 3/4 |
| Reliability | 300/300 hostile or random inputs handled, 0 unapproved sends, slowest 8.0 s; 30/30 concurrent requests answered; 7/7 graceful replies with the model server down (p95 16 ms) |

**Other measurements (separate benchmark scripts):**

- **Retrieval** over 2,000 messages: p50 45 ms, p95 94 ms, recall@5 1.0.
- **Crash recovery**: state restored in ~3.3 s after `kill -9` (gate 5 s).
- **Resource use** under load: 4.9% mean / 12.2% peak of the machine (gate 30%).
- **Skill-scroll routing**: 29/29 on the tuning set, 11/14 held-out.

Confidence intervals are Wilson 95%; small samples are reported as such, and every miss is listed in `docs/EVALS.md` so the numbers can be checked.

---

## Repository layout

```
kala-dhua/
├─ src/ragnarok/              # the agent itself
│  ├─ core/                   # clock, ids, errors, logging, failpoints
│  ├─ config/                 # data directories and paths
│  ├─ store/                  # SQLCipher db, crypto, migrations, vectors, selftest
│  ├─ memory/                 # 11 memory types, retrieval, delete, retention, ledger
│  ├─ llm_gateway/            # the only path to models
│  ├─ gateway_scan/           # prompt & egress scanning (fast + deep)
│  ├─ router/                 # L0 → L1 → L2 → L3 cascade + route library
│  │  └─ backends/            # laya, ollama_small, fake
│  ├─ harness/                # planner, executor, verifier, recovery, digest, vision, calc
│  ├─ policy/                 # declarative rules + taint tracking (fail closed)
│  ├─ broker/                 # click-gated approvals and autonomy modes
│  ├─ security/               # guard, vault, canaries, snapshots, identities
│  ├─ sandbox/                # seatbelt / container / windows backends
│  │  └─ suite/               # shared attack suite
│  ├─ connectors/             # gmail, calendar, drive, meet, slack, telegram, discord, outlook, files, notifications
│  ├─ ingest/                 # queue + parse + sanitize + chunk + index
│  ├─ daemon/                 # always-on runtime, local API, proactive, dream, demo
│  ├─ platform/               # screen, voice, ocr, notify, power, procs, secrets
│  ├─ skills/                 # builtin + native skills (hash-verified)
│  ├─ scrolls/                # Markdown specialists + dreaming author
│  ├─ evidence/               # crash-lab evidence helpers
│  └─ ui/                     # the always-on-top window (index.html + mascot.html)
├─ evals/                     # benchmark suites (utility, intent, actions, tasks, security, …)
├─ tests/                     # unit, security, and browser-level UI smoke tests
├─ scripts/                   # spikes, evals, e2e, and live probes
├─ docs/                      # architecture, build plan, ADRs, spikes, setup, evidence
├─ evidence/                  # Laya calibration data and router synth set
├─ models.lock                # pinned model names + digests
├─ pyproject.toml             # dependencies + the ragnarok entry point
└─ CLAUDE.md                  # operating rules for the build
```

---

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — what and why; locked decisions **D-01 … D-30**.
- [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) — the ordered, testable build plan with gates.
- [`CLAUDE.md`](CLAUDE.md) — operating rules for the build (binding).
- [`docs/CONNECTOR_SETUP.md`](docs/CONNECTOR_SETUP.md) — step-by-step connector setup.
- [`docs/SKILL_SCROLLS.md`](docs/SKILL_SCROLLS.md) — skill scrolls and idle-time dreaming.
- [`docs/EVALS.md`](docs/EVALS.md) — the measured evaluation report (regenerated by `ragnarok evals`).
- [`docs/REQUIREMENTS_TRACE.md`](docs/REQUIREMENTS_TRACE.md) — every requirement of the original problem statement, its implementation, evidence and gaps.
- [`docs/BRIEF_MID_EVAL.md`](docs/BRIEF_MID_EVAL.md) — measured numbers, security posture, and the demo script.
- [`docs/HANDOVER_TUSHAR.md`](docs/HANDOVER_TUSHAR.md) — Windows handover and honest gap list.
- [`docs/adr/`](docs/adr/) — one decision record per deviation.
- [`docs/spikes/`](docs/spikes/) — measured spike results (Laya, Ollama, calibration).

---

## Status

Under active construction for a hackathon deadline of **09:00 IST, 26 October 2026**.

**Done (verified live on real accounts, with evidence in `docs/evidence/`):** encrypted store and memory (view, edit, export, verified delete), Zero Trust layer, executor with crash recovery (state back in ~3.3 s after `kill -9`), the four-tier router that learns routes, deterministic multi-step decomposition, always-on triggers and proactivity, read connectors (Gmail, Calendar, Drive, Meet, Slack, Telegram, Discord) and click-gated writes (email, reply, draft, calendar, Drive, Telegram, Discord, Slack), live per-app ingestion, a context-grouped Digest with a timeline, explain-my-screen and picture questions with a local vision model, local multilingual voice, skill scrolls and idle-time dreaming, user-defined skills, a sandboxed Python skill, and a benchmark harness with ten eval suites.

**Honest gaps:** Windows sandbox backends and notification reader, Laya fine-tuning (it is calibrated, not fine-tuned), Outlook and notifications on real accounts, the full red-team report, and the 90-simulated-day pruning run. The requirement-by-requirement trace against the original problem statement is in [`docs/REQUIREMENTS_TRACE.md`](docs/REQUIREMENTS_TRACE.md).

See [`docs/HANDOVER_TUSHAR.md`](docs/HANDOVER_TUSHAR.md) for the full list.

