"""Deterministic fake gateway for unit tests and CI: no model, no network."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from typing import Any

import numpy as np

from ragnarok.llm_gateway.client import ChatResult, Priority

DIM = 64
Responder = Callable[[list[dict[str, str]], dict | None], Any]


def hash_embedding(text: str, dim: int = DIM) -> np.ndarray:
    """Bag-of-words hashing embedding: similar words give similar vectors, identical on every OS."""
    vec = np.zeros(dim, dtype=np.float32)
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        h = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "big")
        vec[h % dim] += 1.0 if (h >> 32) & 1 else -1.0
        vec[(h >> 8) % dim] += 0.5
    n = float(np.linalg.norm(vec))
    return vec / n if n else vec


class FakeGateway:
    def __init__(self, responder: Responder | None = None) -> None:
        self.responder = responder or (lambda messages, schema: {} if schema else "ok")
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self, *, role: str, messages: list[dict[str, str]], schema: dict | None, max_tokens: int,
        deadline: float, priority: Priority, seed: int | None = 0, temperature: float = 0.0,
    ) -> ChatResult:
        self.calls.append({"role": role, "messages": messages, "schema": schema})
        out = self.responder(messages, schema)
        if schema is not None:
            import json

            return ChatResult(json.dumps(out), out, "fake", 0, 0, 0.0, role)
        return ChatResult(str(out), None, "fake", 0, 0, 0.0, role)

    async def embed(self, texts: list[str], *, deadline: float, priority: Priority) -> np.ndarray:
        return np.stack([hash_embedding(t) for t in texts]) if texts else np.zeros((0, DIM), dtype=np.float32)
