---
name: action-items
title: Action items
description: Pulls the tasks, owners and deadlines out of any text.
version: 1
triggers:
  - list the action items
  - what do I need to do from this
  - extract the tasks from this
  - todo list from these notes
tools: []
permissions: [read]
min_model: small
memory: shared
examples:
  - input: "Extract the tasks: Priya will book the venue by Tuesday. I need to send the budget to Meera. Nobody owns the catering yet."
    contains: ["Priya", "venue", "catering"]
    max_words: 100
---
Extract tasks from the text. One line each: "[ ] task - owner - date". Use "unassigned" when no owner is named and "no date" when none is given. Include a line for EVERY task in the text, also the ones nobody owns (write "unassigned" for those). Include only things that are actually in the text. Do not add suggestions.
