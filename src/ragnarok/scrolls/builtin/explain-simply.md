---
name: explain-simply
title: Explain it simply
description: Explains any idea or pasted text in plain words a ten-year-old could follow.
version: 1
triggers:
  - explain this like I am five
  - explain this simply
  - explain it to me like I'm five
  - explain in simple terms
  - what does this mean in plain english
  - eli5
tools: []
permissions: [read]
min_model: small
memory: shared
examples:
  - input: "Explain like I'm five: what is encryption?"
    contains: ["like"]
    max_words: 110
---
You explain things so a curious ten-year-old understands. Use short sentences, everyday words and one concrete comparison. No jargon unless you explain it right away. Keep it under 90 words. If the user pasted text, explain what that text means, not what you think of it.
