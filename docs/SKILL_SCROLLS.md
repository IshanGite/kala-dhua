# Skill Scrolls and idle-time dreaming

One local model, many specialists. Each specialist is a short Markdown file (a *scroll*). Only the headers stay in memory; the full text of a scroll is loaded only when it is chosen.

## Requirements (extend FR2; see REQUIREMENTS_TRACE.md)

| Req | Statement |
|---|---|
| FR2.4 Scroll files | A skill can be defined as a `.md` file: a YAML header (name, description, trigger examples, read-only tools, permissions, minimum model size, memory scope, chain, self-test examples) and a plain-language body. The agent indexes the headers only and loads a scroll's full text on demand (progressive loading). |
| FR2.5 Routing, chaining, permissions | A message is matched to a scroll by embedding similarity to the scroll's trigger examples (or by naming it: "use the explain-simply scroll on ..."). A scroll may chain to other scrolls (text in, text out, at most three deep, loops refused). A scroll holds only the `read` permission and may only request read-only lookups from a fixed allowlist (`memory.search`, `memory.recent`, `calendar.agenda`) that CODE performs; it can never send, post, schedule or change anything. |
| FR2.6 Memory and tests | Each scroll has its own memory namespace (a separate compartment) in addition to shared memory. Each scroll carries example inputs with expected content; the agent can self-test a scroll after an edit or a model change and records the result with the model's name. A header may state the minimum model size and the router escalates to the larger local model when required. |
| FR2.7 Self-written scrolls and trust | The agent drafts new scrolls from requests the user keeps making, and revised scrolls when a scroll's own tests fail. A draft is validated, tested on its own examples, and shown as a diff; it is used only after a person clicks Approve, and the approval is pinned to the exact text (any edit returns it to *pending*). A scroll file is untrusted text until approved; the injection scanner flags suspicious wording for the reader. |
| FR1.4 Idle-time dreaming | When the computer is plugged in and the user has been away for a set time, the agent replays the day (and writes a day summary into memory), runs memory consolidation, rehearses failed tasks and misunderstood requests as dry runs, and drafts improvements to its own skill scrolls. It never acts outside the computer, stops when the user returns or unplugs, has a time budget, and reports in the Inbox. |

## File format

```
---
name: meeting-notes                 # 3-41 chars, lowercase, digits, dashes; must match the file name
title: Meeting notes
description: Turns rough meeting notes into decisions, owners and next steps.   # one sentence
version: 1
triggers:                           # what someone would say to use it (1-12 examples)
  - summarize these meeting notes
  - what were the decisions from the meeting
tools: []                           # read-only lookups done by code: memory.search, memory.recent, calendar.agenda
permissions: [read]                 # only `read` exists
min_model: small                    # small | medium | large (large escalates to the bigger local model)
memory: shared                      # own | shared | both
chain: []                           # other scrolls to hand the output to (max 3)
examples:                           # self-tests
  - input: "Ravi will ship Friday. Asha needs review."
    contains: ["Ravi", "Friday"]
    max_words: 140
---
Instructions in plain words (at most 6,000 characters: small models follow long instructions poorly).
```

Eight scrolls ship with the app (`src/ragnarok/scrolls/builtin/`): meeting-notes, email-triage, reply-drafter, triage-and-reply (a chain), explain-simply, action-items, decision-helper, standup-writer. Yours live in the app's data folder under `scrolls/`.

## How a message becomes a specialist

1. Headers only are in memory (about 600 tokens for eight scrolls, measured by a character estimate).
2. The message is embedded and compared with each approved scroll's trigger examples (about 45 ms once the examples are cached). A scroll needs a similarity of 0.62 and a clear lead over the runner-up; at 0.75 or more it also wins over a summary or a lookup.
3. A request to DO something (send, remind, schedule) is decided first by the normal understanding and planning path and is never turned into a text-only scroll.
4. The chosen scroll's full text becomes the instructions for the same local model; the user's text and anything read from memory are placed inside labelled delimiters as data that cannot give instructions.
5. The reply shows which scroll was used in "Why?" and how many tokens loading it cost.

## Safety properties (each has a test)

* A scroll cannot list a write tool: the file is rejected (`tools` allowlist, `permissions: [read]`).
* A scroll is *pending* until a person approves the exact text; editing it, or a user file with the name of a shipped scroll, returns it to pending.
* Approval happens by a click in the window on the API with the scroll's hash; the agent has no way to approve.
* A reply from a scroll goes through the same filter that stops the model claiming it sent something.
* The self-tests run without tools (hermetic) so they do not depend on what happens to be in memory.
* A scroll chain that loops back on itself stops.

## Dreaming

Conditions (all must hold): dreaming is on; the computer is on AC power (or "also on battery" is on; an unknown power state does not dream); neither the chat nor the keyboard and mouse have been used for the set time (default 10 minutes); at least six hours since the last dream unless a lot happened. "Dream now" in Sources > Dreaming bypasses the waiting, not the safety.

Phases: replay (day summary into memory as a note from source "dream", plus open loops and stated preferences as suggestions), consolidate (the daily consolidation job if due), rehearse (failed tasks and requests the agent could not handle are re-read as a dry run: the sentence is parsed and a plan is built and validated, nothing executes), scrolls (self-test the longest-untested scrolls; for a failing one draft a revision and keep it only if it passes more of its own tests; draft at most one new scroll from repeated requests).

A dream holds no sender, no executor and no approval power (checked structurally in `tests/unit/test_dream.py`).

## Measured (scripts/eval_scrolls.py, scripts/qa_scrolls_dream.py; real local models; author-written sets)

See `docs/evidence/scrolls.json`. Routing on the 29 messages the triggers were tuned on, and on 14 held-out messages written afterwards; each builtin scroll's own self-test; the token saving of progressive loading against one prompt containing every scroll. These sets are small and written by one person: a regression guard, not a benchmark.
