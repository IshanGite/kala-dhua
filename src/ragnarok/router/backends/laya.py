"""Laya backend (D-04): a local decision model, not an LLM. One forward pass answers several typed questions with probabilities.

Runs in-process or in its own worker process (laya_worker.py) over a local pipe. It NEVER opens a network port (we do not use `laya-serve`,
which binds all interfaces by default). Laya is not served by Ollama: this is the one place a non-Ollama model is used, only for
classification, and `router.laya_backend = ollama_small` swaps it for an Ollama-served model through the same interface.

Result shape (from the Laya README): result["answers"][qid] holds `choice` + `probabilities` + `confidence`, a `score` level, or a `noul` P(true).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from ragnarok.core.clock import get_clock
from ragnarok.core.errors import ModelError
from ragnarok.router.types import Answer, Question


def to_laya_spec(questions: list[Question]) -> dict[str, dict[str, Any]]:
    spec: dict[str, dict[str, Any]] = {}
    for q in questions:
        if q.type == "choice":
            spec[q.id] = {"type": "choice", "instructions": q.instructions, "criteria": dict(q.options)}
        elif q.type == "score":
            spec[q.id] = {"type": "score", "instructions": q.instructions, "criteria": list(q.options) or ["low", "medium", "high"]}
        else:
            spec[q.id] = {"type": "noul", "instructions": q.instructions}
    return spec


def from_laya(result: dict[str, Any], questions: list[Question]) -> list[Answer]:
    answers = result.get("answers", {})
    out: list[Answer] = []
    for q in questions:
        raw = answers.get(q.id)
        if raw is None:
            out.append(Answer(q.id, "" if q.type == "choice" else False, 0.0))
        elif q.type == "choice":
            probs = {str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()}
            label = str(raw.get("choice", ""))
            conf = float(raw.get("confidence", probs.get(label, 0.0)))
            out.append(Answer(q.id, label, conf, probs))
        elif q.type == "noul":
            p = float(raw["noul"] if isinstance(raw, dict) else raw)
            out.append(Answer(q.id, p >= 0.5, max(p, 1 - p), {"true": p, "false": 1 - p}))
        else:
            out.append(Answer(q.id, float(raw.get("score", 0.0)), float(raw.get("confidence", 0.5))))
    return out


def best_device() -> str:
    """Spike 9 (docs/spikes/laya.md): MPS is ~2x faster than CPU on Apple silicon; CUDA where present; CPU everywhere else (Windows default)."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


class LayaBackend:
    name = "laya"

    def __init__(self, agent: Any | None = None, *, model_id: str = "convaiinnovations/laya", subfolder: str = "typed-decisions", device: str | None = None) -> None:
        self._agent = agent
        self.device = device
        self.model_id, self.subfolder = model_id, subfolder
        self._lock: asyncio.Lock | None = None

    @staticmethod
    def installed() -> bool:
        import importlib.util

        return importlib.util.find_spec("laya") is not None

    async def _get(self) -> Any:
        if self._agent is not None:
            return self._agent
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._agent is None:
                def load() -> Any:
                    os.environ.setdefault("USE_TF", "0")  # an installed TensorFlow can deadlock model construction
                    try:
                        import laya
                    except ImportError as exc:
                        raise ModelError("laya is not installed (uv sync --extra laya)", code="E_LAYA_MISSING") from exc
                    ckpt = os.environ.get("RAGNAROK_LAYA_CKPT")  # a local fine-tune (scripts/finetune_laya.py); opt-in, the shipped checkpoint is the default
                    if ckpt:
                        return laya.load(ckpt, device=self.device or best_device())
                    return laya.load(self.model_id, subfolder=self.subfolder, device=self.device or best_device())

                self._agent = await asyncio.to_thread(load)
        return self._agent

    async def warmup(self) -> None:
        await self._get()

    async def decide(self, text: str, questions: list[Question], *, deadline: float) -> list[Answer]:
        remaining = deadline - get_clock().monotonic()
        if remaining <= 0:
            raise ModelError("deadline passed", code="E_MODEL_DEADLINE")
        agent = await self._get()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(agent.predict, text[:4000], to_laya_spec(questions)), timeout=remaining)
        except TimeoutError as exc:
            raise ModelError("laya timed out", code="E_MODEL_TIMEOUT", retryable=True) from exc
        except Exception as exc:  # noqa: BLE001
            raise ModelError(f"laya failed: {type(exc).__name__}", code="E_LAYA_FAILED") from exc
        return from_laya(result, questions)
