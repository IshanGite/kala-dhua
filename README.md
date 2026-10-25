# Ragnarok (kala-dhua)

A sovereign, always-on personal AI agent that runs entirely on your own machine: local models through Ollama, encrypted memory with provenance, sandboxed skills, and writes that happen only when you click.

Status: under active construction for a hackathon deadline of 09:00 IST, 26 October 2026. The design is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and the ordered work list is in [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md).

## Quickstart

macOS: `brew install uv git` and install the Ollama app. Windows: `winget install astral-sh.uv Git.Git Ollama.Ollama`.

```
git clone https://github.com/IshanGite/kala-dhua.git
cd kala-dhua
uv sync
uv run ragnarok doctor
```

`uv run ragnarok doctor` is the sync tool: run it after every pull on every machine.

## Prior work

Connector parsing and polling logic is adapted from the groupify project (github.com/abhi-dev99/groupify), with all cloud-model and hosted-service dependencies removed.
