"""Shared pieces for every eval suite: an isolated world with the real local models and recording fake senders, statistics, and result files.
Environment is set before ragnarok is imported so a suite can never touch the user's real data: a temporary data folder, no connected sources, an in-memory vault."""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "evidence" / "evals"


def isolate() -> None:
    os.environ.setdefault("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["RAGNAROK_HOME"] = tempfile.mkdtemp(prefix="ragnarok-eval-")
    os.environ["RAGNAROK_SKIP_SOURCES"] = "telegram,slack,discord,gmail,gcal,gdrive,gmeet,outlook,outlook_cal,files,notifications"
    sys.path.insert(0, str(REPO / "src"))


isolate()
from ragnarok.core.clock import get_clock  # noqa: E402
from ragnarok.daemon.chat import ChatService  # noqa: E402
from ragnarok.daemon.wiring import World, build_world, warm_up  # noqa: E402
from ragnarok.platform.secrets import MemoryStore, token_name  # noqa: E402

SENT: list[tuple[str, tuple[Any, ...]]] = []


def sender(kind: str):  # noqa: ANN201
    async def f(*a: Any, **k: Any) -> str:
        SENT.append((kind, a))
        return "https://example.test/" + kind if kind in ("drive", "calendar") else "ok-" + kind
    return f


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - h), 3), round(min(1.0, c + h), 3))


def prop(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "ci95": list(wilson(k, n))}


def pctl(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * len(s)))], 1) if s else 0.0


def lat(xs: list[float]) -> dict[str, float]:
    return {"n": len(xs), "p50_ms": pctl(xs, 0.5), "p95_ms": pctl(xs, 0.95), "max_ms": round(max(xs), 1) if xs else 0.0}


async def make_world(*, mirror: bool = True, laya: bool = True, sandbox: bool = True, gateway: Any = None, seed: Any = None) -> World:
    vault = MemoryStore()
    if mirror:
        vault.set(token_name("google", "write"), json.dumps({"client_id": "e", "client_secret": "e", "refresh_token": "e"}))  # only so calendar copies are offered; every send is a recording fake
    kinds = ("mail", "mail_reply", "gmail_draft", "calendar", "drive", "telegram", "discord", "slack")
    w = build_world(vault=vault, sandbox=sandbox, use_laya=laya, gateway=gateway, senders={k: sender(k) for k in kinds}, max_cpu=101)
    await warm_up(w)
    if hasattr(w.l1, "warmup"):
        try:
            await w.l1.warmup()
        except Exception:  # noqa: BLE001
            pass
    if seed is not None:
        await seed(w)
    w.services.chat = ChatService(w.agent, w.services.hub, w.services.proactive.triggers)
    return w


class Approver:
    """Clicks every approval card the way a person would: approve or deny. Records the cards, so what was shown can be compared with what was done."""

    def __init__(self, w: World, approve: bool = True, tainted_ok: bool = True) -> None:
        self.w, self.approve, self.cards, self.tainted_ok = w, approve, [], tainted_ok
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while True:
            for c in self.w.services.hub.pending():
                self.cards.append(c)
                self.w.services.hub.decide(c["id"], self.approve and not c.get("stepup"))
            await asyncio.sleep(0.02)

    def __enter__(self) -> "Approver":
        self._task = asyncio.get_running_loop().create_task(self._loop())
        return self

    def __exit__(self, *a: Any) -> None:
        if self._task:
            self._task.cancel()


async def ask(w: World, text: str, timeout: float = 120.0) -> tuple[dict[str, Any], float]:
    t = time.perf_counter()
    r = await asyncio.wait_for(w.services.chat.submit(text, quick_s=timeout), timeout)
    n = 0
    while r.get("pending") and time.perf_counter() - t < timeout:
        await asyncio.sleep(0.2)
        r = w.services.chat.poll(r["job"])
        n += 1
    return r, (time.perf_counter() - t) * 1000


def save(result: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    result["finished_at"] = get_clock().now_ms()
    (OUT / f"{result['suite']}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def models() -> dict[str, str]:
    d = json.loads((REPO / "models.lock").read_text(encoding="utf-8"))
    return {k: v["name"] for k, v in d.items()}
