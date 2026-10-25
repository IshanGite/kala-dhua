"""Deterministic intent rules for commands whose wording is unambiguous. A small arbiter model picks the wrong skill too often (measured: docs/spikes/e2e_full.md),
so strong lexical cues are matched by code first. Rules only SELECT a skill; the policy engine, the guard and the approval card still decide whether it runs.
Order matters: a platform word means a chat message, an address means a mail."""

from __future__ import annotations

import re

_PLAT = r"(?:telegram|discord|slack)"
RULES: list[tuple[str, re.Pattern[str]]] = [(s, re.compile(p, re.I)) for s, p in [
    ("reminder.add", r"^\s*(?:please\s+)?(?:can you\s+)?(?:remind me|set (?:a )?reminder|add (?:a )?(?:to-?do|reminder))\b"),
    ("reminder.list", r"\b(?:what|which|show|list)\b.*\b(?:reminders?|to-?dos?)\b"),
    ("chat.reply", rf"\b(?:tell|message|text|ping|dm|say to)\b.+\b(?:on|in|via)\s+{_PLAT}\b|\b(?:post|say|send|write)\b.+\b(?:in|on|to)\s+(?:the\s+)?{_PLAT}\b|\b(?:reply|respond)\b.+\b(?:on|in)\s+{_PLAT}\b"),
    ("mail.reply", r"^\s*(?:please\s+)?(?:can you\s+)?(?:reply|respond|answer|write back)\s+to\b"),
    ("mail.send", r"^\s*(?:please\s+)?(?:can you\s+)?(?:send|email|mail)\b.*\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    ("mail.draft", r"^\s*(?:please\s+)?(?:can you\s+)?(?:draft|write|compose|prepare)\s+(?:an?\s+|the\s+)?(?:email|mail|message)\b.*\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    ("calendar.add", r"\b(?:put|add|schedule|book|block|create)\b.*\b(?:on|to|in|into)\s+(?:my\s+|the\s+)?(?:google\s+)?calendar\b"),
    ("drive.create", r"\b(?:create|write|save|make)\b.*\b(?:docs?|documents?|notes?|files?)\b.*\b(?:in|on|to)\s+(?:my\s+)?(?:google\s+)?drive\b|\bin (?:my\s+)?(?:google\s+)?drive\b.*\b(?:create|write|save)\b"),
]]


def match_intent(text: str) -> str | None:
    for skill, pat in RULES:
        if pat.search(text):
            return skill
    return None
