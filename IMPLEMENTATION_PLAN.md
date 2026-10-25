# IMPLEMENTATION_PLAN.md — operating rules for building Ragnarok

You are building Ragnarok per `docs/ARCHITECTURE.md` (what/why, decisions D-01…D-30 are binding) and `docs/BUILD_PLAN.md` (order, gates, interfaces). These rules exist so you never have to guess.

## The never-guess protocol
1. If the spec states it, do it. If a Decision (D-nn) covers it, obey it.
2. If the spec is silent, choose the **simplest option that keeps every gate green**, write a 5-line ADR in `docs/adr/NNN-title.md` (context, choice, why, how to reverse), and continue.
3. Stop and ask the user **only** for: items in BUILD_PLAN §0 that block the work, anything needing an install/download approval, or a conflict between two Decisions. Ask once, batched.
4. Never invent facts about libraries, APIs or model behaviour. Check the installed version (`--help`, docs, a spike) and write the result in `docs/spikes/`.
5. Unknown external platform detail (OAuth scopes, rate limits, hostnames, Slack/Discord/Telegram settings): verify against current official docs at build time; record the finding in the connector's header comment.

## Hard prohibitions
- No cloud AI service, SDK, key or endpoint. No Groq, Gemini, OpenAI, Hugging Face at runtime, Fireflies, OSV.dev at runtime. (D-10, D-22)
- Only `llm_gateway` talks to models. Only the Memory Service writes memory SQL. Skills and connectors never touch the DB. (CI lint enforces)
- Never log content. Logs contain codes and ids only.
- Never store secrets in files, DB, env of skills, prompts or memory. Tokens live in the OS vault; read and write tokens are separate.
- Never let a model decide permissions. Policy rules decide; Laya and the route library can only add caution.
- Never run unsandboxed skills. `dev-unsafe` only with `RAGNAROK_ENV=dev --unsafe-dev`, shows a banner, refused in acceptance.
- Never claim a number you did not measure. No vendor benchmarks as facts.
- Never copy code from AGPL projects (PyMuPDF, Khoj) or from other repos. groupify is the user's own repo: follow BUILD_PLAN §0 Q4.
- Never force-push main, backdate commits, or add padding commits. No co-author trailers.
- Never weaken a gate to make it pass. If a gate cannot pass, mark it honestly in the report and tell the user.

## Working loop
1. `git pull`, read the phase in BUILD_PLAN, create tasks with TaskCreate for each numbered item.
2. Write the **test first** (names in BUILD_PLAN §8), then the code, then run `uv run ragnarok check` (< 1 min).
3. Commit small (conventional prefix), push every 20–40 min, tag at block ends.
4. At each gate run the gate tests; record numbers into `evidence/` via the harness, not by hand.
5. Use subagents/worktrees for independent streams (e.g. connectors vs sandbox vs UI); merge through PRs on green CI.
6. Before finishing a phase: update README status table (generated), `docs/spikes/` or ADRs, and the demo script steps added by the phase.

## Style and robustness
- Python 3.12, type hints, `mypy --strict` on policy/broker/security/store/router; pydantic models at every boundary; pure functions for decisions.
- Every external call has a timeout, a watchdog, structured errors with `retryable` flag, and a defined degraded behaviour ("degrade, never crash").
- Every state change is written to disk before the next action; every side effect uses intent → effect → confirm with an idempotency key.
- UTC ms in storage; `Clock` service for all time; seeded randomness; UTF-8 everywhere; pathlib + platformdirs; `subprocess` with arg lists; `spawn` start method.
- Untrusted text is data: wrap in labelled delimiters, carry trust tags, never place raw untrusted text in planner prompts (use handles).

## When something fails
Fix the cause, not the test. If blocked > 20 min, write the problem into `docs/spikes/blockers.md`, apply the fallback listed in BUILD_PLAN §11, and move on. Never leave `main` red.

## Final checklist before tagging v1.0
`ragnarok acceptance --full` green or honestly annotated · SEC suite results in `docs/RED_TEAM.md` · learning-curve + ablation plots · `docs/EVIDENCE.md` merged · network audit + Wi-Fi-off demo recorded · README table generated · no secrets, weights or personal data in repo · tag pushed by 08:30 IST.
