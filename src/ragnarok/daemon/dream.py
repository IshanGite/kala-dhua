"""Idle-time dreaming. When the computer is plugged in and you have been away for a while, the agent uses the quiet time to get better at helping you:

1. REPLAY the day: what you asked, what was done, what is still open, written into memory as a day summary you can ask about.
2. CONSOLIDATE memory (the daily consolidation and forgetting job, early if it is due).
3. REHEARSE what went wrong: tasks that failed, and requests it did not understand, are re-read offline (a dry run: nothing is executed) to see whether it would handle them now.
4. DRAFT improvements to its own skill scrolls: revisions for scrolls whose own tests fail, and new scrolls for things you keep asking.

A dream never sends, posts, schedules or changes anything outside this computer. It holds no sender, no executor and no approval power. Every result is a note, a day summary, or a DRAFT that waits in your Inbox
for you to read as a diff and approve. It stops the moment you come back, unplug, or switch dreaming off, and it has a time budget.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ragnarok.core.clock import get_clock
from ragnarok.core.errors import ModelError
from ragnarok.core.logging import log
from ragnarok.ingest.pipeline import ingest_text
from ragnarok.llm_gateway.client import Priority
from ragnarok.platform import power as power_mod

DAY_MS = 86_400_000
BUDGET_S = 600.0
FAILURE_MARKERS = ("I did not do anything", "I do not know that conversation", "Which email address", "What should the email say", "I did not quite get that", "Who should I message",
                   "I could not tell what time", "What should I remind you", "I could not work out what to do")
SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}, "topics": {"type": "array", "items": {"type": "string"}, "maxItems": 5}, "open_loops": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                                          "preferences": {"type": "array", "items": {"type": "string"}, "maxItems": 3}}, "required": ["summary"]}


class Dreamer:
    def __init__(self, *, mem: Any, reader: Any, ledger: Any, state: Any, inbox: Any, gateway: Any, index: Any, runner: Any, maintenance: Any, rehearse: Any, compartment: str = "work", data_dir: Path | None = None,
                 power: Any = power_mod) -> None:
        self._mem, self._r, self._ledger, self._s, self._inbox, self._gw, self._idx = mem, reader, ledger, state, inbox, gateway, index
        self._runner, self._maint, self._rehearse, self._comp, self._power = runner, maintenance, rehearse, compartment, power
        self._dir = (data_dir or Path(".")) / "dream"
        self.last_activity = get_clock().now_ms()
        self.running = False
        self._abort = False
        self._cache: tuple[float, float | None] = (0.0, None)
        self.progress = ""

    # ------------------------------------------------------------------ settings and status
    def settings(self) -> dict[str, Any]:
        return {"enabled": self._s.get("dream_enabled", "1") == "1", "idle_min": int(self._s.get("dream_idle_min", "10") or 10), "battery_ok": self._s.get("dream_battery", "0") == "1"}

    def configure(self, *, enabled: bool | None = None, idle_min: int | None = None, battery_ok: bool | None = None) -> None:
        if enabled is not None:
            self._s.set("dream_enabled", "1" if enabled else "0")
            if not enabled:
                self._abort = True
        if idle_min is not None:
            self._s.set("dream_idle_min", str(max(1, min(int(idle_min), 240))))
        if battery_ok is not None:
            self._s.set("dream_battery", "1" if battery_ok else "0")

    def notify_activity(self) -> None:
        """The person is using the agent: a dream in progress stops at its next step."""
        self.last_activity = get_clock().now_ms()
        if self.running:
            self._abort = True

    def _system_idle(self) -> float | None:
        now = time.monotonic()
        if now - self._cache[0] > 20:
            self._cache = (now, self._power.idle_seconds())
        return self._cache[1]

    def eligibility(self, *, force: bool = False) -> tuple[bool, str]:
        st = self.settings()
        if self.running:
            return False, "already dreaming"
        if force:
            return True, "started by you"
        if not st["enabled"]:
            return False, "dreaming is switched off"
        ac = self._power.on_ac()
        if ac is False and not st["battery_ok"]:
            return False, "waiting for the charger"
        if ac is None and not st["battery_ok"]:
            return False, "cannot tell whether the computer is plugged in"
        idle_need = st["idle_min"] * 60
        chat_idle = (get_clock().now_ms() - self.last_activity) / 1000
        sys_idle = self._system_idle()
        if chat_idle < idle_need or (sys_idle is not None and sys_idle < idle_need):
            return False, f"waiting until you have been away {st['idle_min']} minutes"
        last = int(self._s.get("dream_last", "0") or 0)
        busy = len(self._r.user_messages(self._r.compartment_id(self._comp) or "", max(last, get_clock().now_ms() - DAY_MS), 50)) if self._r.compartment_id(self._comp) else 0
        if get_clock().now_ms() - last < 6 * 3_600_000 and busy < 12:
            return False, "dreamed recently and not much has happened since"
        return True, "plugged in and idle"

    def status(self) -> dict[str, Any]:
        ok, why = self.eligibility()
        last = int(self._s.get("dream_last", "0") or 0)
        rep: dict[str, Any] = {}
        try:
            rep = json.loads(self._s.get("dream_report", "{}") or "{}")
        except ValueError:
            pass
        return {**self.settings(), "plugged_in": self._power.on_ac(), "idle_seconds": self._system_idle(), "eligible": ok, "reason": why, "running": self.running, "progress": self.progress, "last_at": last or None, "report": rep}

    # ------------------------------------------------------------------ the loop
    async def tick(self) -> None:
        """Called every few seconds by the housekeeping loop. A dream can take minutes, so it runs as its own task: reminders and proactive alerts keep firing while it works."""
        import asyncio

        if getattr(self, "_task", None) is not None and not self._task.done():
            return
        ok, _ = self.eligibility()
        if ok:
            self._task = asyncio.get_running_loop().create_task(self.run())

    def _stop(self) -> bool:
        if self._abort:
            return True
        st = self.settings()
        return not st["enabled"] or (self._power.on_ac() is False and not st["battery_ok"] and not self._forced)

    async def run(self, *, force: bool = False) -> dict[str, Any]:
        ok, why = self.eligibility(force=force)
        if not ok:
            return {"skipped": why}
        self.running, self._abort, self._forced = True, False, force
        t0 = time.monotonic()
        rep: dict[str, Any] = {"at": get_clock().now_ms(), "phases": {}, "stopped_early": False}
        steps = [("replay", self._replay), ("consolidate", self._consolidate), ("rehearse", self._rehearse_phase), ("scrolls", self._scrolls_phase)]
        try:
            for name, fn in steps:
                if self._stop() or time.monotonic() - t0 > BUDGET_S:
                    rep["stopped_early"] = True
                    break
                self.progress = {"replay": "Replaying the day", "consolidate": "Consolidating memory", "rehearse": "Rehearsing what went wrong", "scrolls": "Working on skill scrolls"}[name]
                try:
                    rep["phases"][name] = await fn(t0)
                except Exception as exc:  # noqa: BLE001 - one phase failing never stops the next
                    log("dream_phase_failed", phase=name, err=type(exc).__name__)
                    rep["phases"][name] = {"error": type(exc).__name__}
            rep["seconds"] = round(time.monotonic() - t0, 1)
            self._s.set("dream_last", str(get_clock().now_ms()))
            self._s.set("dream_report", json.dumps(rep)[:8000])
            self._announce(rep)
            log("dream_done", seconds=rep["seconds"], early=rep["stopped_early"])
        finally:
            self.running, self.progress = False, ""
        return rep

    # ------------------------------------------------------------------ 1. replay the day
    async def _replay(self, t0: float) -> dict[str, Any]:
        cid = self._r.compartment_id(self._comp)
        since = get_clock().now_ms() - DAY_MS
        evs = self._r.day_events(cid, since) if cid else []
        users = [e for e in evs if e["kind"] == "user_message"]
        tasks = self._ledger.recent_tasks(since, ("succeeded", "failed", "partial", "cancelled"), 60)
        by_state: dict[str, int] = {}
        for t in tasks:
            by_state[t["state"]] = by_state.get(t["state"], 0) + 1
        date = datetime.fromtimestamp(get_clock().now_ms() / 1000).strftime("%Y-%m-%d")
        out: dict[str, Any] = {"messages": len(users), "tasks": by_state}
        if not users and not tasks:
            return {**out, "summary": ""}
        lines = [f"{'You' if e['kind'] == 'user_message' else 'Ragnarok'}: {e['text'][:140]}" for e in evs[-40:]]
        lines += [f"Task ({t['state']}): {t['goal'][:100]}" for t in tasks[:12]]
        data: dict[str, Any] = {}
        try:
            res = await self._gw.chat(role="arbiter", schema=SCHEMA, max_tokens=420, deadline=get_clock().monotonic() + 40, priority=Priority.BACKGROUND, seed=0, temperature=0.2,
                                      messages=[{"role": "system", "content": "You write a short private diary entry about one day of an assistant and its user, from the log lines. Summary: 2 to 4 plain sentences, only what the log shows. "
                                                                               "topics: up to 5 short topics. open_loops: things the user asked for that do not look finished. preferences: stable preferences the user clearly stated (else empty). JSON only."},
                                               {"role": "user", "content": "Log:\n<<<\n" + "\n".join(lines) + "\n>>>"}])
            data = res.parsed or {}
        except ModelError:
            pass
        summary = str(data.get("summary", "")).strip() or f"{len(users)} request(s) from you and {len(tasks)} task(s) ({', '.join(f'{v} {k}' for k, v in by_state.items()) or 'none'})."
        topics = [str(t)[:60] for t in data.get("topics", [])][:5]
        loops = [str(t)[:120] for t in data.get("open_loops", [])][:5]
        prefs = [str(t)[:120] for t in data.get("preferences", [])][:3]
        body = f"Day summary for {date}. {summary}" + (f" Topics: {', '.join(topics)}." if topics else "") + (f" Still open: {'; '.join(loops)}." if loops else "")
        await ingest_text(self._mem, self._gw, self._idx, source="dream", native_id=f"day:{date}", compartment=self._comp, trust="agent", text=body, title=f"Day summary {date}", kind="summary", priority=Priority.BACKGROUND)
        return {**out, "summary": summary, "topics": topics, "open_loops": loops, "preferences": prefs}

    # ------------------------------------------------------------------ 2. consolidate
    async def _consolidate(self, t0: float) -> dict[str, Any]:
        if self._maint is None:
            return {}
        rep = self._maint.run()  # the existing daily consolidation and forgetting job, only if it is due
        return {"ran": bool(rep), **{k: v for k, v in (rep or {}).items() if isinstance(v, int)}}

    # ------------------------------------------------------------------ 3. rehearse
    async def _rehearse_phase(self, t0: float) -> dict[str, Any]:
        cid = self._r.compartment_id(self._comp)
        since = get_clock().now_ms() - 3 * DAY_MS
        cases: list[tuple[str, str]] = []
        for t in self._ledger.recent_tasks(since, ("failed", "partial"), 8):
            cases.append((t["goal"], f"task {t['state']}"))
        if cid:
            evs = self._r.day_events(cid, since, 600)
            for i, e in enumerate(evs[:-1]):
                nxt = evs[i + 1]
                if e["kind"] == "user_message" and nxt["kind"] == "assistant_message" and any(m in nxt["text"] for m in FAILURE_MARKERS) and len(e["text"]) > 8:
                    cases.append((e["text"], "not understood"))
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        for text, why in cases:
            if text in seen or len(results) >= 8 or self._stop() or time.monotonic() - t0 > BUDGET_S:
                continue
            seen.add(text)
            ok, note = await self._rehearse(text)  # a dry run: reads the sentence and builds a plan, executes nothing
            results.append({"text": text[:120], "before": why, "now_ok": ok, "note": note[:140]})
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            (self._dir / "rehearsals.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
        except OSError:
            pass
        return {"rehearsed": len(results), "would_work_now": sum(1 for r in results if r["now_ok"]), "still_failing": [r for r in results if not r["now_ok"]][:3]}

    # ------------------------------------------------------------------ 4. scrolls
    async def _scrolls_phase(self, t0: float) -> dict[str, Any]:
        from ragnarok.scrolls import author

        out: dict[str, Any] = {"tested": [], "drafts": []}
        lib = self._runner.lib
        st = lib._state().get("tests", {})
        todo = [s for s in lib.usable() if s.examples][:6]
        todo.sort(key=lambda s: st.get(s.name, {}).get("at", 0))  # the longest-untested first
        for s in todo[:3]:
            if self._stop() or time.monotonic() - t0 > BUDGET_S:
                break
            p, t, _ = await self._runner.self_test(s.name)
            out["tested"].append({"name": s.name, "passed": p, "total": t})
            if t and p < t and len(out["drafts"]) < 2:
                d = await author.propose_revision(self._gw, self._runner, s.name)
                if d:
                    out["drafts"].append({"id": d["id"], "name": d["name"], "reason": d["reason"], "kind": "revision"})
                    self._inbox.add("attention", f"Improved scroll ready: {d['name']}", d["reason"] + " Open Sources > Skill scrolls to read the change and approve it.", key=f"scroll-draft:{d['id']}")
        cid = self._r.compartment_id(self._comp)
        if cid and not self._stop() and time.monotonic() - t0 < BUDGET_S:
            texts = [m["text"] for m in self._r.user_messages(cid, get_clock().now_ms() - 30 * DAY_MS, 400)]
            for d in await author.propose_new(self._gw, self._runner, texts, max_new=1):
                out["drafts"].append({"id": d["id"], "name": d["name"], "reason": d["reason"], "kind": "new"})
                self._inbox.add("attention", f"New scroll proposed: {d['name']}", d["reason"] + " Open Sources > Skill scrolls to read it and approve it.", key=f"scroll-draft:{d['id']}")
        return out

    # ------------------------------------------------------------------ the morning note
    def _announce(self, rep: dict[str, Any]) -> None:
        ph = rep.get("phases", {})
        lines = []
        rp = ph.get("replay", {})
        if rp.get("summary"):
            lines.append(f"Yesterday in short: {rp['summary']}")
        if rp.get("open_loops"):
            lines.append("Still open: " + "; ".join(rp["open_loops"]))
        if rp.get("preferences"):
            lines.append("You seem to prefer: " + "; ".join(rp["preferences"]) + " (say 'remember that ...' if that is right)")
        rh = ph.get("rehearse", {})
        if rh.get("rehearsed"):
            lines.append(f"I re-read {rh['rehearsed']} thing(s) that went wrong; {rh['would_work_now']} would work now.")
        sc = ph.get("scrolls", {})
        if sc.get("tested"):
            lines.append("Scroll self-tests: " + ", ".join(f"{t['name']} {t['passed']}/{t['total']}" for t in sc["tested"]))
        if sc.get("drafts"):
            lines.append(f"{len(sc['drafts'])} skill draft(s) are waiting for your approval.")
        if not lines:
            lines.append("Nothing needed attention.")
        self._inbox.add("briefing", "Dream report: what I did while you were away", "\n".join(lines), key=f"dream:{get_clock().now_ms() // 3_600_000}")
