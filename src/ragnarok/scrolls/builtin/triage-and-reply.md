---
name: triage-and-reply
title: Triage, then draft the first reply
description: Looks at your recent messages, picks the most urgent one, and drafts a reply for it (a chain of two scrolls).
version: 1
triggers:
  - triage my inbox and draft the first reply
  - what needs me most and how should I answer
tools: [memory.recent]
permissions: [read]
min_model: small
memory: shared
chain: [reply-drafter]
---
From the recent messages in the context (or the text given), pick the ONE item that most needs a response: something with a deadline or a direct question. Write it as: "From <sender>: <what they need>" followed by the quoted message text. Do not draft the reply yourself; the next specialist does that. If nothing needs a response, say so.
