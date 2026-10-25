"""Deterministic keyword backend for tests, CI and as a rules-only fallback. Calibrated by construction (0.55 to 0.95)."""

from __future__ import annotations

import re

from ragnarok.router.types import Answer, Question

KEYWORDS: dict[str, tuple[str, ...]] = {
    "mail": ("email", "mail", "inbox", "reply", "draft", "send", "message from"),
    "calendar": ("calendar", "meeting", "schedule", "reschedule", "slot", "invite", "event"),
    "files": ("file", "folder", "document", "pdf", "read", "search for", "find the"),
    "notes": ("note", "remember", "jot", "remind"),
    "code": ("python", "script", "code", "calculate", "compute"),
    "chat": ("slack", "telegram", "discord", "channel", "post to"),
    "memory": ("what did i", "last week", "yesterday", "do you remember", "what do you know"),
    "system": ("status", "health", "settings", "doctor"),
}
INJECTION = re.compile(r"ignore (all |any )?(previous|prior)|disregard the system|reveal your (system )?prompt|forward (all|every)", re.I)
MULTI = re.compile(r"\b(and then|then|after that|also|check .+ and|find .+ and)\b", re.I)


class FakeBackend:
    name = "fake"

    def __init__(self, fixed: dict[str, tuple[object, float]] | None = None) -> None:
        self.fixed = fixed or {}
        self.calls = 0

    async def decide(self, text: str, questions: list[Question], *, deadline: float) -> list[Answer]:
        self.calls += 1
        low = text.lower()
        out: list[Answer] = []
        for q in questions:
            if q.id in self.fixed:
                v, p = self.fixed[q.id]
                out.append(Answer(q.id, v, p))
            elif q.id in ("skill_family", "skill_in_family"):
                scores = {k: sum(w in low for w in KEYWORDS.get(k, ())) for k in q.options} or {}
                best = max(scores, key=lambda k: scores[k]) if scores else ""
                hit = scores.get(best, 0)
                p = 0.9 if hit >= 2 else 0.72 if hit == 1 else 0.3
                out.append(Answer(q.id, best if hit else "", p, {k: (0.9 if k == best and hit else 0.1) for k in q.options}))
            elif q.id == "needs_plan":
                v = bool(MULTI.search(text)) or len(text.split()) > 22
                out.append(Answer(q.id, v, 0.85))
            elif q.id == "injection":
                v = bool(INJECTION.search(text))
                out.append(Answer(q.id, v, 0.95 if v else 0.9))
            elif q.id == "risk_precheck":
                high = any(w in low for w in ("send", "delete", "forward", "pay", "post"))
                out.append(Answer(q.id, "high" if high else "none", 0.8))
            else:
                out.append(Answer(q.id, False, 0.5))
        return out
