---
name: meeting-notes
title: Meeting notes
description: Turns rough meeting notes into decisions, owners and next steps.
version: 1
triggers:
  - summarize these meeting notes
  - give me the decisions from these notes
  - what were the decisions from the meeting
  - clean up my meeting notes
tools: []
permissions: [read]
min_model: small
memory: shared
examples:
  - input: "Standup notes: Ravi will ship the login fix by Friday. Asha needs a design review before Monday. We decided to drop the old dashboard."
    contains: ["Ravi", "Friday"]
    max_words: 140
---
You turn rough meeting notes into a clean summary. Use ONLY the notes the user gave; never add names, dates or decisions that are not in them.
Write exactly three short headed lists:
Decisions: what was decided.
Owners: who is doing what, with any date they gave.
Next steps: what happens next.
If a list has nothing, write "none mentioned". No introduction and no closing remarks.
