"""Cassette gateway: record real model answers once, replay them in CI on any OS.

Key = sha256(model role + messages + schema + seed + max_tokens + temperature). Cassettes are UTF-8
JSON Lines with LF endings. A miss in replay mode is an error, never a silent guess.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ragnarok.core.errors import ModelError
from ragnarok.core.ids import sha256_hex
from ragnarok.llm_gateway.client import ChatResult, Gateway, Priority


def chat_key(role: str, messages: list[dict[str, str]], schema: dict | None, seed: int | None, max_tokens: int, temperature: float) -> str:
    return sha256_hex(json.dumps([role, messages, schema, seed, max_tokens, temperature], sort_keys=True))


def embed_key(texts: list[str]) -> str:
    return sha256_hex(json.dumps(["embed", texts]))


class CassetteGateway:
    def __init__(self, path: Path, *, record_with: Gateway | None = None) -> None:
        self.path = path
        self._inner = record_with
        self._store: dict[str, Any] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._store[rec["key"]] = rec["value"]

    def _append(self, key: str, value: Any) -> None:
        self._store[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"key": key, "value": value}, sort_keys=True) + "\n")

    async def chat(
        self, *, role: str, messages: list[dict[str, str]], schema: dict | None, max_tokens: int,
        deadline: float, priority: Priority, seed: int | None = 0, temperature: float = 0.0, images: list[str] | None = None,
    ) -> ChatResult:
        key = chat_key(role, messages, schema, seed, max_tokens, temperature)
        if key in self._store:
            v = self._store[key]
            return ChatResult(v["text"], v["parsed"], v["model"], 0, 0, 0.0, role)
        if self._inner is None:
            raise ModelError("cassette miss", code="E_CASSETTE_MISS")
        res = await self._inner.chat(role=role, messages=messages, schema=schema, max_tokens=max_tokens,
                                     deadline=deadline, priority=priority, seed=seed, temperature=temperature, images=images)
        self._append(key, {"text": res.text, "parsed": res.parsed, "model": res.model})
        return res

    async def embed(self, texts: list[str], *, deadline: float, priority: Priority) -> np.ndarray:
        key = embed_key(texts)
        if key in self._store:
            return np.asarray(self._store[key], dtype=np.float32)
        if self._inner is None:
            raise ModelError("cassette miss", code="E_CASSETTE_MISS")
        arr = await self._inner.embed(texts, deadline=deadline, priority=priority)
        self._append(key, np.round(arr, 6).tolist())
        return arr
