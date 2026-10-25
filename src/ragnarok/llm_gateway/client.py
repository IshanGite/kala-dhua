"""LLM Gateway: chat() and embed() against Ollama on loopback, nothing else.

Rules enforced here (ARCHITECTURE D-03, NFR1.3, BUILD_PLAN section 3 lint rule 1):
* the base URL must be a loopback address;
* only models named in models.lock may be used;
* num_ctx is set explicitly on every call and the prompt is checked to fit;
* structured output uses a JSON schema; every call records model, purpose, tokens and latency;
* interactive requests outrank background ones and cancel a running background call if needed.
"""

from __future__ import annotations

import asyncio
import enum
import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import numpy as np

from ragnarok.core.clock import get_clock
from ragnarok.core.errors import ModelError, NetworkGuardError
from ragnarok.core.logging import log
from ragnarok.llm_gateway.models import ModelLock

LOOPBACK_NAMES = {"localhost"}
DEFAULT_URL = "http://127.0.0.1:11434"
# Starting context sizes per role, tuned by measured memory use (ARCHITECTURE 6.3).
NUM_CTX = {"planner": 8192, "arbiter": 4096, "extractor": 4096, "judge": 2048, "embedder": 2048}


class Priority(enum.IntEnum):
    INTERACTIVE = 0
    TRIGGERED = 1
    BACKGROUND = 2


@dataclass
class ChatResult:
    text: str
    parsed: Any | None
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    role: str = ""


class Gateway(Protocol):
    async def chat(
        self, *, role: str, messages: list[dict[str, str]], schema: dict | None, max_tokens: int,
        deadline: float, priority: Priority, seed: int | None = 0, temperature: float = 0.0,
    ) -> ChatResult: ...

    async def embed(self, texts: list[str], *, deadline: float, priority: Priority) -> np.ndarray: ...


def assert_loopback(url: str) -> None:
    host = urlparse(url).hostname or ""
    if host in LOOPBACK_NAMES:
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise NetworkGuardError(f"model endpoint {host!r} is not a loopback address")


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_thinking(text: str) -> str:
    """Some qwen3 builds write their reasoning into the reply even with thinking switched off: never show it, never parse it."""
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()


def approx_tokens(text: str) -> int:
    """Cheap upper-bound estimate used only to check that a prompt fits the context."""
    return len(text) // 3 + 1


@dataclass
class CallRecord:
    role: str
    model: str
    purpose: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float


@dataclass
class _Slots:
    background: asyncio.Semaphore
    interactive: asyncio.Semaphore
    running_background: set[asyncio.Task] = field(default_factory=set)


class OllamaGateway:
    def __init__(
        self,
        lock: ModelLock,
        base_url: str = DEFAULT_URL,
        *,
        background_slots: int = 1,
        interactive_slots: int = 2,
        keep_alive: str = "24h",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        assert_loopback(base_url)
        self.base_url = base_url.rstrip("/")
        self.lock = lock
        self.keep_alive = keep_alive
        self.records: list[CallRecord] = []
        self._slots = _Slots(asyncio.Semaphore(background_slots), asyncio.Semaphore(interactive_slots))
        # trust_env=False: never pick up a proxy from the environment for model traffic.
        self._client = client or httpx.AsyncClient(trust_env=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _timeout(self, deadline: float) -> float:
        remaining = deadline - get_clock().monotonic()
        if remaining <= 0:
            raise ModelError("deadline already passed", code="E_MODEL_DEADLINE", retryable=False)
        return remaining

    async def _post(self, path: str, body: dict, deadline: float, priority: Priority) -> dict:
        timeout = self._timeout(deadline)
        if priority == Priority.BACKGROUND:
            async with self._slots.background:
                task = asyncio.current_task()
                if task:
                    self._slots.running_background.add(task)
                try:
                    return await self._do_post(path, body, timeout)
                finally:
                    if task:
                        self._slots.running_background.discard(task)
        # Interactive work cancels a running background call so it never waits behind one.
        for t in list(self._slots.running_background):
            t.cancel()
        async with self._slots.interactive:
            return await self._do_post(path, body, timeout)

    async def _do_post(self, path: str, body: dict, timeout: float) -> dict:
        try:
            r = await self._client.post(self.base_url + path, json=body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ModelError("model call timed out", code="E_MODEL_TIMEOUT", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"model endpoint unreachable: {type(exc).__name__}", retryable=True) from exc
        if r.status_code != 200:
            raise ModelError(f"model endpoint returned {r.status_code}", retryable=r.status_code >= 500)
        return r.json()

    async def chat(
        self, *, role: str, messages: list[dict[str, str]], schema: dict | None, max_tokens: int,
        deadline: float, priority: Priority, seed: int | None = 0, temperature: float = 0.0,
    ) -> ChatResult:
        entry = self.lock.for_role(role)
        self.lock.check_name(entry.name)
        ctx = NUM_CTX.get(role, 4096)
        prompt_tokens_est = sum(approx_tokens(m["content"]) for m in messages)
        if prompt_tokens_est + max_tokens > ctx:
            raise ModelError("prompt does not fit the context window", code="E_CTX_OVERFLOW")
        body: dict[str, Any] = {
            "model": entry.name,
            "messages": messages,
            "stream": False,
            "think": False,  # qwen3-class models reason silently by default and spend the whole token cap before answering (spike 2)
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": ctx, "num_predict": max_tokens, "temperature": temperature, "seed": seed or 0},
        }
        if schema is not None:
            body["format"] = schema
        started = get_clock().monotonic()
        data = await self._post("/api/chat", body, deadline, priority)
        latency = (get_clock().monotonic() - started) * 1000
        text = strip_thinking(data.get("message", {}).get("content", ""))
        parsed = None
        if schema is not None:
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                raise ModelError("model returned invalid JSON", code="E_MODEL_BAD_JSON", retryable=True) from exc
        res = ChatResult(text, parsed, entry.name, data.get("prompt_eval_count", 0), data.get("eval_count", 0), latency, role)
        self.records.append(CallRecord(role, entry.name, "chat", res.prompt_tokens, res.completion_tokens, latency))
        log("llm_call", role=role, ms=round(latency), ptok=res.prompt_tokens, ctok=res.completion_tokens)
        return res

    async def embed(self, texts: list[str], *, deadline: float, priority: Priority) -> np.ndarray:
        entry = self.lock.for_role("embedder")
        self.lock.check_name(entry.name)
        started = get_clock().monotonic()
        data = await self._post("/api/embed", {"model": entry.name, "input": texts, "keep_alive": "24h"}, deadline, priority)
        latency = (get_clock().monotonic() - started) * 1000
        arr = np.asarray(data["embeddings"], dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        arr = arr / np.where(norms == 0, 1, norms)
        self.records.append(CallRecord("embedder", entry.name, "embed", 0, 0, latency))
        return arr
