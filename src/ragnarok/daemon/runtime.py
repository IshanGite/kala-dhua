"""The always-on part: continuous ingestion from every connected source, an on/off switch, and an activity feed (codes and counts, never content)."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any

import psutil

from ragnarok.connectors.runner import ConnectorRunner, CycleReport
from ragnarok.core.clock import get_clock
from ragnarok.core.logging import log
from ragnarok.memory.commitments import Commitments
from ragnarok.memory.runtime_state import RuntimeState

PAIRED = {"telegram"}  # sources where an unknown sender is held until the user approves
# Seconds between polls per source. Telegram long-polls (the call itself waits for a message), so it is effectively instant; the rest are cheap list calls.
DEFAULT_INTERVALS = {"telegram": 0.2, "slack": 4.0, "discord": 4.0, "gmail": 4.0, "outlook": 8.0, "gcal": 30.0, "outlook_cal": 60.0, "gdrive": 60.0, "gmeet": 180.0, "notifications": 3.0, "files": 5.0}
BURST_S = 1.0  # right after something arrived, look again quickly: messages come in bursts


@dataclass
class Governor:
    """Admission control: do not start a sync cycle while the machine is busy (NFR2.2)."""

    max_cpu: float = 60.0

    def admit(self) -> bool:
        return psutil.cpu_percent(interval=None) < self.max_cpu


class Runtime:
    def __init__(self, state: RuntimeState, runners: dict[str, ConnectorRunner], *, interval_s: float = 60.0, governor: Governor | None = None, commitments: Commitments | None = None,
                 intervals: dict[str, float] | None = None) -> None:
        self.state, self.runners, self.interval = state, runners, interval_s
        self.intervals = dict(intervals or {})  # per source; a source not listed uses interval_s
        self._tasks: list[asyncio.Task[None]] = []
        self.commitments = commitments
        self.maintenance: Any = None  # FR1.3: consolidation and forgetting, once a day
        self.digest: Any = None  # the digest engine: its model rewrites are refreshed in the background
        self._digest_seen = ""
        self.proactive: Any = None  # set by the wiring: the FR4 engine (triggers, heads-ups, briefing)
        self.reminders: deque[dict[str, Any]] = deque(maxlen=50)
        self.governor = governor or Governor()
        self.activity: deque[dict[str, Any]] = deque(maxlen=200)
        self.last: dict[str, dict[str, Any]] = {}
        self._stop = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self.state.get("enabled", "1") == "1"

    def set_enabled(self, on: bool) -> None:
        self.state.set("enabled", "1" if on else "0")
        self._note("toggle", "-", "on" if on else "off")

    def _note(self, kind: str, source: str, code: str, **counts: int) -> None:
        self.activity.appendleft({"ts": get_clock().now_ms(), "kind": kind, "source": source, "code": code, **counts})

    def fire_reminders(self) -> list[dict[str, Any]]:
        """Due reminders fire whether or not ingestion is switched on: they are the user's own words, not read from anywhere."""
        if self.commitments is None:
            return []
        due = self.commitments.fire_due()
        for d in due:
            item = {"ts": get_clock().now_ms(), "kind": "reminder", "source": "reminder", "code": "due", "text": d["what"]}
            self.reminders.appendleft(item)
            self.activity.appendleft(item)
            if self.proactive is not None:
                self.proactive.inbox.add("reminder", f"Reminder: {d['what'][:80]}", d["what"], key=f"rem:{d['id']}", os_notify=self.proactive.settings()["os_notify"])
        return due

    async def housekeeping(self) -> None:
        """Reminders, proactivity and the daily maintenance: independent of ingestion, so they run even while a slow source is waiting."""
        self.fire_reminders()
        if self.proactive is not None:
            try:
                await self.proactive.run()
            except Exception as exc:  # noqa: BLE001 - proactivity must never stop ingestion
                log("proactive_failed", err=type(exc).__name__)
        if self.maintenance is not None and self.maintenance.due():
            try:
                rep = self.maintenance.run()  # one small transaction a day: same thread as the database connection
                if rep:
                    self._note("maintenance", "memory", "ok", **{k: v for k, v in rep.items() if isinstance(v, int)})
            except Exception as exc:  # noqa: BLE001 - housekeeping must never stop the agent
                log("maintenance_failed", err=type(exc).__name__)

    async def _cycle_one(self, name: str, runner: ConnectorRunner) -> CycleReport | None:
        try:
            rep = await runner.cycle()
        except Exception as exc:  # noqa: BLE001 - one broken source never stops the others
            code = getattr(exc, "code", type(exc).__name__)
            log("runtime_cycle_failed", src=name, err=code)
            prev = self.last.get(name, {})
            if prev.get("code") != str(code):  # the feed shows a change of state, not the same error every few seconds
                self._note("error", name, str(code))
            self.last[name] = {"ok": False, "code": str(code), "at": get_clock().now_ms(), "since": prev.get("since") or get_clock().now_ms(), "interval": self._interval(name)}
            return None
        now = get_clock().now_ms()
        self.last[name] = {"ok": True, "at": now, "ingested": rep.ingested, "held": rep.held, "failed": rep.failed, "interval": self._interval(name),
                           "last_new": now if (rep.ingested or rep.held) else self.last.get(name, {}).get("last_new")}
        if rep.ingested or rep.held or rep.failed:
            self._note("ingested", name, "ok", ingested=rep.ingested, held=rep.held, failed=rep.failed)
        return rep

    async def tick(self) -> dict[str, CycleReport]:
        """One pass over everything, all sources at once (the Sync now button, and the tests)."""
        out: dict[str, CycleReport] = {}
        await self.housekeeping()
        if not self.enabled:
            return out
        names = [n for n in self.runners if self.governor.admit() or self._note("skipped", n, "busy")]
        reps = await asyncio.gather(*[self._cycle_one(n, self.runners[n]) for n in names])
        for n, rep in zip(names, reps, strict=True):
            if rep is not None:
                out[n] = rep
        return out

    def _interval(self, name: str) -> float:
        return self.intervals.get(name, self.interval)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(seconds, 0.01))
        except TimeoutError:
            pass

    async def _poll(self, name: str, runner: ConnectorRunner) -> None:
        """One endless loop per source: a slow or failing source never delays another. Fast after activity, backing off on errors."""
        backoff = 0.0
        while not self._stop.is_set():
            if not (self.enabled and self.governor.admit()):
                await self._sleep(1.0)
                continue
            work = asyncio.ensure_future(self._cycle_one(name, runner))
            stop = asyncio.ensure_future(self._stop.wait())
            await asyncio.wait({work, stop}, return_when=asyncio.FIRST_COMPLETED)  # a long poll must not delay shutdown
            if not work.done():
                work.cancel()
                break
            stop.cancel()
            rep = work.result()
            if rep is None:
                backoff = min(max(backoff * 2, 2.0), 60.0)
                await self._sleep(backoff)
            else:
                backoff = 0.0
                await self._sleep(min(BURST_S, self._interval(name)) if (rep.ingested or rep.held) else self._interval(name))

    async def _refine_digest(self) -> None:
        """Low priority and only when the content changed: the most important sections get a validated model rewrite; the deterministic digest is always there meanwhile."""
        if self.digest is None or not self.enabled:
            return
        d = self.digest.build(24)
        if d["fingerprint"] == self._digest_seen or not d["sections"]:
            return
        try:
            await self.digest.refine(d, max_sections=3)
            self._digest_seen = d["fingerprint"]
        except Exception as exc:  # noqa: BLE001 - a summary improvement must never disturb ingestion
            log("digest_refine_failed", err=type(exc).__name__)

    async def _aux(self) -> None:
        n = 0
        while not self._stop.is_set():
            await self.housekeeping()
            n += 1
            if n % 6 == 0:  # about every 30 s
                await self._refine_digest()
            await self._sleep(5.0)

    def start(self) -> None:
        if not self._tasks:
            self._stop.clear()
            loop = asyncio.get_running_loop()
            self._tasks = [loop.create_task(self._aux())] + [loop.create_task(self._poll(n, r)) for n, r in self.runners.items()]

    async def stop(self) -> None:
        self._stop.set()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks = []

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
        return {"enabled": self.enabled, "interval_s": self.interval, "sources": {n: {**self.last.get(n, {"ok": None}), "interval": self._interval(n)} for n in self.runners},
                "held": [{"platform": p, "handle": h} for p, h in self.state.held_senders()], "reminders": list(self.reminders)[:5]}
