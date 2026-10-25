"""Skill shortlist (FR2.2): embed each skill's description and examples once, then the top-k by cosine for a request.

The model never sees the whole catalogue (small models stay accurate with at most five tools). Embeddings go through the gateway.
"""

from __future__ import annotations

import numpy as np

from ragnarok.llm_gateway.client import Gateway, Priority
from ragnarok.skills.manifest import Manifest


def skill_text(m: Manifest) -> str:
    ex = " ".join(str(e.params) + " " + e.note for e in m.examples[:3])
    return f"{m.name}: {m.description} {ex}".strip()


class SkillSelector:
    def __init__(self, gateway: Gateway) -> None:
        self._gw = gateway
        self._names: list[str] = []
        self._mat = np.zeros((0, 0), dtype=np.float32)

    async def index(self, manifests: list[Manifest], deadline: float) -> None:
        self._names = [m.name for m in manifests]
        if manifests:
            self._mat = await self._gw.embed([skill_text(m) for m in manifests], deadline=deadline, priority=Priority.BACKGROUND)

    def top(self, qvec: np.ndarray, k: int = 5) -> list[tuple[str, float]]:
        if not self._names:
            return []
        scores = self._mat @ qvec
        order = np.argsort(-scores)[:k]
        return [(self._names[i], float(scores[i])) for i in order]
