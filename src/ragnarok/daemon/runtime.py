"""The always-on part: continuous ingestion from every connected source, an on/off switch, and an activity feed (codes and counts, never content)."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import psutil

from ragnarok.connectors.runner import ConnectorRunner, CycleReport
from ragnarok.core.clock import get_clock
from ragnarok.core.logging import log
from ragnarok.memory.runtime_state import RuntimeState

PAIRED = {"telegram"}  # sources where an unknown sender is held until the user approves


@dataclass
class Governor:
    """Admission control: do not start a sync cycle while the machine is busy (NFR2.2)."""

    max_cpu: float = 60.0

    def admit(self) -> bool:
        return psutil.cpu_percent(interval=None) < self.max_cpu


class Runtime:
    def __init__(self, state: RuntimeState, runners: dict[str, ConnectorRunner], *, interval_s: float = 60.0, governor: Governor | None = None) -> None:
        self.state, self.runners, self.interval = state, runners, interval_s
        self.governor = governor or Governor()
        self.activity: deque[dict[str, Any]] = deque(maxlen=200)
        self.last: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self.state.get("enabled", "1") == "1"

    def set_enabled(self, on: bool) -> None:
        self.state.set("enabled", "1" if on else "0")
        self._note("toggle", "-", "on" if on else "off")

    def _note(self, kind: str, source: str, code: str, **counts: int) -> None:
        self.activity.appendleft({"ts": get_clock().now_ms(), "kind": kind, "source": source, "code": code, **counts})

    async def tick(self) -> dict[str, CycleReport]:
        out: dict[str, CycleReport] = {}
        if not self.enabled:
            return out
        for name, runner in self.runners.items():
            if not self.governor.admit():
                self._note("skipped", name, "busy")
                continue
            try:
                rep = await runner.cycle()
            except Exception as exc:  # noqa: BLE001 - one broken source never stops the others
                code = getattr(exc, "code", type(exc).__name__)
                log("runtime_cycle_failed", src=name, err=code)
                self._note("error", name, str(code))
                self.last[name] = {"ok": False, "code": str(code), "at": get_clock().now_ms()}
                continue
            out[name] = rep
            self.last[name] = {"ok": True, "at": get_clock().now_ms(), "ingested": rep.ingested, "held": rep.held, "failed": rep.failed}
            if rep.ingested or rep.held or rep.failed:
                self._note("ingested", name, "ok", ingested=rep.ingested, held=rep.held, failed=rep.failed)
        return out

    async def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            await self.tick()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(self.interval - (time.monotonic() - started), 0.01))
            except TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    def approve_sender(self, platform: str, handle: str) -> int:
        r = self.runners.get(platform)
        n = r.approve_sender(handle) if r else 0
        self._note("paired", platform, "approved")
        return n

    def block_sender(self, platform: str, handle: str) -> None:
        if r := self.runners.get(platform):
            r.block_sender(handle)
            self._note("paired", platform, "blocked")

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "interval_s": self.interval, "sources": {n: self.last.get(n, {"ok": None}) for n in self.runners},
                "held": [{"platform": p, "handle": h} for p, h in self.state.held_senders()]}
