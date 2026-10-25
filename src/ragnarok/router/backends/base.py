"""The DecisionBackend interface and calibration helpers."""

from __future__ import annotations

import math
from typing import Protocol

from ragnarok.router.types import Answer, Question


class DecisionBackend(Protocol):
    name: str

    async def decide(self, text: str, questions: list[Question], *, deadline: float) -> list[Answer]: ...


def softmax(logits: list[float], temperature: float = 1.0) -> list[float]:
    t = max(temperature, 1e-6)
    m = max(logits)
    exps = [math.exp((x - m) / t) for x in logits]
    s = sum(exps)
    return [e / s for e in exps]


def temper(probabilities: dict[str, float], temperature: float) -> dict[str, float]:
    """Temperature scaling of an over-confident distribution (Laya ships over-confident; thresholds need a fitted temperature)."""
    keys = list(probabilities)
    logits = [math.log(max(probabilities[k], 1e-9)) for k in keys]
    return dict(zip(keys, softmax(logits, temperature), strict=True))
