# Ragnarok

**A sovereign, always-on personal AI agent that runs entirely on your own machine.**

![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![AI](https://img.shields.io/badge/AI-local%20only-2ea44f)
![Storage](https://img.shields.io/badge/storage-SQLCipher-orange)
![Status](https://img.shields.io/badge/status-under%20construction-yellow)

Ragnarok reads your **mail, calendar, files and chats**, remembers them **with provenance**, plans and executes multi-step tasks through **sandboxed skills** — and never sends, deletes or changes anything outside its workspace **without a click bound to the exact content**.

Local models. Encrypted memory. Zero Trust. Every write gated behind a click.

---

## Why Ragnarok?

Five principles guide every feature:

| Pillar | What it means |
|---|---|
| **Sovereignty you can prove** | One data directory, loopback-only services, an egress allowlist, packet-capture evidence, and an offline demo. |
| **Memory with provenance** | Eleven memory types in a single encrypted file; every fact links to a quote in a source; delete cascades and is verified by a scan. |
| **Safe autonomy** | Zero Trust: the model is assumed already tricked. Code-enforced policy, sandboxing, taint tracking, and content-bound approvals. |
| **Fast by design** | A confidence-gated cascade; most decisions never reach a generative model, and the cascade learns from its own successes. |
| **Evidence, not claims** | Every number comes from this project's own harness, per machine, merged into `docs/EVIDENCE.md`. |

---

## What it does

- **Ingests** Gmail, Google Calendar, Drive, Meet, Slack, Telegram, Discord, Outlook, local files, and desktop notifications.
- **Remembers** everything in an encrypted SQLCipher store with provenance, bitemporal facts, and hash-chained events.
- **Routes** every request through a four-tier cascade (learned routes → Laya → small model → big model), so repeat tasks get faster.
- **Plans and executes** multi-step tasks: a request like *"look up what Dev needs and remind me about it tomorrow"* is split into an execution graph by code in about 0.1 s (the LLM planner is only the fallback), with crash recovery, retries and one self-correcting re-plan.
- **Works on its own, within limits**: user-defined triggers (*"every day at 8am catch me up"*, *"when I get an email from Dev, tell me"*), heads-ups before meetings, "needs your attention" flags, a daily briefing, and an inbox — autonomous runs are never trusted more than typed ones.
- **You control the memory**: search, edit, export (JSON or Markdown with provenance) and permanently delete (preview, confirmation token, verified receipt) from the window; every reply has a **Why?** panel.
- **Skills**: `ragnarok skill new/add/list`, turn on with a step-up approval, run in a sandbox.
- **Guards every write** behind an approval card bound to the exact content — sends, deletes and edits happen only when you click.
- **Serves a local UI** (always-on-top window) plus a CLI for everything.

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
uv run ragnarok doctor
```

`uv run ragnarok doctor` is the **sync tool**: run it after every pull, on every machine. `uv run ragnarok check` is the pre-push gate.

### Run it

```bash
uv run ragnarok run       # always-on agent + local API (no window)
uv run ragnarok window    # always-on-top chat/activity window with an on/off switch
```

On an empty store the agent answers *"I don't have anything about that in memory yet"*.

---

## CLI

| Command | Purpose |
|---|---|
| `ragnarok doctor [--benchmark] [--restore-test]` | Check this machine. Run after every pull. |
| `ragnarok check` | Lint + fast tests (the pre-push gate). |
| `ragnarok run [--port] [--interval] [--folder]` | Start the always-on agent + local API. |
| `ragnarok window [--port] [--no-top]` | Start the always-on-top window. |
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
| Telegram | ✅ | Send message |
| Discord | ✅ | Post in channel |
| Outlook | ✅ (coded, not yet live-tested) | Draft & send (designed) |
| Local files | ✅ | — |
| Desktop notifications | Built, not yet live-tested | — |

See **[`docs/CONNECTOR_SETUP.md`](docs/CONNECTOR_SETUP.md)** for the exact console steps for every source.

---

## Models

Pinned in [`models.lock`](models.lock) (name + digest); the daemon refuses a model whose digest differs.

| Role | Model |
|---|---|
| Embeddings | `nomic-embed-text` |
| Writer (chat & drafts) | `qwen3:0.6b` |
| Arbiter (L2) | `qwen3:1.7b` |
| Planner (L3) | `qwen3:4b` |

No cloud AI service, SDK, key or endpoint. Generation and embeddings go only to Ollama on `127.0.0.1:11434`.

---

## Security & privacy

- **Local first** — no cloud AI, no telemetry, egress allowlisted and auditable.
- **Encrypted at rest** — SQLCipher, per-file AES-GCM blobs, secrets only in the OS vault.
- **Zero Trust** — identities, hash-verified skills, taint tracking, gateway scanning, canaries, budgets, step-up approval.
- **Reversible** — deletes go to trash, snapshots before bulk changes, an undo window on every write.
- **Evidence** — `tests/security/` doubles as the red-team suite (SEC-01 … SEC-20).

---

## Repository layout

```
kala-dhua/
├─ src/ragnarok/          # the agent itself
│  ├─ core/               # clock, ids, errors, logging, failpoints
│  ├─ store/              # SQLCipher db, crypto, migrations, vectors
│  ├─ memory/             # 11 memory types, retrieval, delete, retention
│  ├─ llm_gateway/        # the only path to models
│  ├─ router/             # L0→L1→L2→L3 cascade + route library
│  ├─ harness/            # planner, executor, verifier, recovery
│  ├─ policy/             # declarative rules (fail closed)
│  ├─ broker/             # click-gated approvals
│  ├─ security/           # guard, vault, canaries, snapshots
│  ├─ sandbox/            # seatbelt / container / windows backends
│  ├─ connectors/         # gmail, calendar, drive, meet, slack, telegram, discord, outlook, files
│  ├─ ingest/             # queue + parse + sanitize + chunk + index
│  ├─ daemon/             # always-on runtime + local API
│  └─ skills/             # builtin + native skills (hash-verified)
├─ ui/                    # the always-on-top window (single HTML page)
├─ tests/                 # unit, contract, security suites
├─ scripts/               # spikes, evals, e2e
├─ docs/                  # architecture, build plan, ADRs, spikes, setup
├─ evidence/              # measured results
├─ models.lock            # pinned model names + digests
├─ pyproject.toml         # dependencies + the ragnarok entry point
└─ CLAUDE.md              # operating rules for the build
```

---

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — what and why; locked decisions **D-01 … D-30**.
- [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) — the ordered, testable build plan with gates.
- [`CLAUDE.md`](CLAUDE.md) — operating rules for the build (binding).
- [`docs/CONNECTOR_SETUP.md`](docs/CONNECTOR_SETUP.md) — step-by-step connector setup.
- [`docs/HANDOVER_TUSHAR.md`](docs/HANDOVER_TUSHAR.md) — Windows handover and honest gap list.
- [`docs/REQUIREMENTS_TRACE.md`](docs/REQUIREMENTS_TRACE.md) — every requirement of the original problem statement, its implementation, evidence and gaps.
- [`docs/BRIEF_MID_EVAL.md`](docs/BRIEF_MID_EVAL.md) — measured numbers, security posture, and the demo script.
- [`docs/adr/`](docs/adr/) — one decision record per deviation.
- [`docs/spikes/`](docs/spikes/) — measured spike results (Laya, Ollama, calibration).

---

## Voice, languages, the raven

The microphone button records and a local Whisper model transcribes it offline in about 99 languages; free-form requests in other languages and with typos are understood. Drag the window to a screen edge to pin it: the window hides and a small raven (Muninn) perches there until you click it. See `docs/BRIEF_MID_EVAL.md`.

## Skill scrolls and dreaming

Specialists are short Markdown files (`src/ragnarok/scrolls/builtin/`); only their headers are kept in memory and the full text loads for the one that fits. When the laptop is plugged in and idle, the agent replays the day, tidies memory, rehearses what failed and drafts improvements to its own scrolls, which wait for your approval. See `docs/SKILL_SCROLLS.md`.

## Demo mode (a safe fallback for any demo)

`ragnarok window --demo` starts a complete copy of the app in a throwaway folder with made-up data for every connected app (Gmail, Slack, Discord, Telegram, Calendar, Drive, Meet, notifications), including a hostile email for the injection demo. Every send is a local fake that only appears in the Activity tab's "Demo outbox"; it holds no real credentials and cannot reach a real account. A DEMO badge is shown the whole time. Sources > "Send me a message now" (and an automatic feed every 50 s) makes new messages arrive live, so the Digest, Inbox and proactive heads-ups can be shown without any real traffic. Close the real window first (both use port 8765, or pass `--port`).

## Status

Under active construction for a hackathon deadline of **09:00 IST, 26 October 2026**.

**Done (verified live on real accounts, with evidence in `docs/evidence/`):** encrypted store and memory (view, edit, export, verified delete), Zero Trust layer, executor with crash recovery (state back in 3.3 s after `kill -9`), the four-tier router that learns routes, deterministic multi-step decomposition, always-on triggers and proactivity, read connectors (Gmail, Calendar, Drive, Meet, Slack, Telegram, Discord) and click-gated writes (email, reply, draft, calendar, Drive, Telegram, Discord, Slack), live per-app ingestion (Telegram long poll, Gmail/Slack/Discord every 4 s), a context-grouped Digest with a timeline, explain-my-screen and picture questions with a local vision model, the always-on window (chat, digest, inbox, approvals, memory, sources/skills), user-defined skills, a sandboxed Python skill, a safe calculator. Retrieval p95 94 ms and classification p95 537 ms on a 2,000-message corpus (gate 800 ms). 360+ automated tests plus a 16-check browser-level UI test.

**Honest gaps:** Windows sandbox backends and notification reader, Laya fine-tuning (it is calibrated, not fine-tuned), Outlook and notifications on real accounts, the full red-team report, and the 90-simulated-day pruning run. The requirement-by-requirement trace against the original problem statement is in [`docs/REQUIREMENTS_TRACE.md`](docs/REQUIREMENTS_TRACE.md).

See [`docs/HANDOVER_TUSHAR.md`](docs/HANDOVER_TUSHAR.md) for the full list.

---

## Prior work

Connector parsing and polling logic is adapted from the [groupify](https://github.com/abhi-dev99/groupify) project, with all cloud-model and hosted-service dependencies removed.
