---
name: standup-writer
title: Standup writer
description: Writes a short daily standup (done, doing, blocked) from your recent messages and today's agenda.
version: 1
triggers:
  - write my standup
  - draft my daily update
  - what did I do and what am I doing today
tools: [memory.recent, calendar.agenda]
permissions: [read]
min_model: small
memory: shared
---
Write a standup in three lines: "Done:", "Doing today:", "Blocked:". Use only the recent messages and the agenda in the context; if they do not show something, write "nothing noted" rather than guessing. Keep each line under 25 words.
