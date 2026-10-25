"""FR4: the agent acts without being asked, within limits. Three jobs, all deterministic and cheap:

* proactivity (FR4.2): heads-up before meetings, 'needs your attention' for mail and messages that ask something or have a deadline, a daily briefing;
* user-defined triggers (FR4.1): cron-style schedules and 'when a message from X arrives' rules, which notify or run a command;
* an inbox the window shows, plus an operating-system notification, so none of it depends on the window being open.

An autonomous run is never trusted more than a typed one: anything it wants to send, post or write waits for the same approval card.
Messages that try to instruct the assistant are reported as suspicious and otherwise ignored.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from ragnarok.core.clock import get_clock
from ragnarok.core.ids import new_id
from ragnarok.core.logging import log
from ragnarok.gateway_scan.scan import scan
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.runtime_state import RuntimeState
from ragnarok.platform.notify import notify

ASK = re.compile(r"\?|\b(?:please|can you|could you|would you|need you to|let me know|reply|respond|confirm|send me|by (?:today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday|eod|end of day)|"
                 r"deadline|due|urgent|asap|waiting for|are we still on|still on for|rsvp)\b", re.I)
CHANNELS = ("gmail", "outlook", "slack", "discord", "telegram")
LEAD_MIN = 30
KEEP = 60
Runner = Callable[[str], Awaitable[str]]


def needs_attention(title: str, preview: str) -> bool:
    return bool(ASK.search(f"{title or ''} {preview or ''}"))


class Inbox:
    """Durable list of things the agent wants to tell the user. Newest first, capped, deduplicated by key."""

    def __init__(self, state: RuntimeState) -> None:
        self._s = state

    def _load(self, key: str) -> list[Any]:
        try:
            return list(json.loads(self._s.get(key, "[]")))
        except ValueError:
            return []

    def items(self) -> list[dict[str, Any]]:
        return [i for i in self._load("inbox") if not i.get("dismissed")]

    def seen(self, key: str) -> bool:
        return key in self._load("inbox_keys")

    def add(self, kind: str, title: str, body: str, *, key: str, source: str = "", link: str | None = None, os_notify: bool = False) -> dict[str, Any] | None:
        if self.seen(key):
            return None
        link = link if isinstance(link, str) and link.startswith("https://") else None  # only web links: never a javascript: or file: URL from outside content
        item = {"id": new_id()[:10], "ts": get_clock().now_ms(), "kind": kind, "title": title[:120], "body": body[:600], "source": source, "link": link, "dismissed": False, "key": key}
        self._s.set("inbox", json.dumps(([item] + self._load("inbox"))[:KEEP]))
        self._s.set("inbox_keys", json.dumps((self._load("inbox_keys") + [key])[-600:]))
        if os_notify:
            notify(title, body)
        return item

    def dismiss(self, item_id: str | None = None) -> int:
        items, n = self._load("inbox"), 0
        for i in items:
            if (item_id is None or i["id"] == item_id) and not i.get("dismissed"):
                i["dismissed"], n = True, n + 1
        self._s.set("inbox", json.dumps(items))
        return n


# ------------------------------------------------------------------ triggers (user-defined)
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_AT = r"(\d{1,2})(?::([0-5]\d))?\s*(am|pm)?"
CRON = re.compile(rf"^\s*(?:please\s+)?(every\s+(?:day|morning|evening|night|weekday|{'|'.join(DAYS)})|daily|each\s+(?:day|morning|evening|{'|'.join(DAYS)}))\s+at\s+{_AT}\s*[,:]?\s*(.+)$", re.I)
WHEN_MSG = re.compile(r"^\s*(?:please\s+)?(?:when(?:ever)?|if)\s+(?:i\s+(?:get|receive)|there\s+is|an?)\s+(?:an?\s+)?(?:new\s+)?(email|mail|message|dm)s?\s+from\s+(.+)$", re.I)
LIST = re.compile(r"\b(?:list|show|what are)\b.*\b(?:triggers?|automations?|schedules?|rules)\b", re.I)
STOP = re.compile(r"\b(?:stop|delete|remove|cancel|turn off)\b.*\b(?:trigger|automation|schedule|rule)s?\b\s*#?(\d+)?", re.I)
BRIEF = re.compile(rf"\b(?:brief me|morning briefing|daily briefing|briefing)\b.*?(?:at\s+{_AT})?\s*$", re.I)


def _hhmm(h: str, m: str | None, ap: str | None) -> str:
    hour = int(h)
    if ap:
        hour = hour % 12 + (12 if ap.lower() == "pm" else 0)
    return f"{hour % 24:02d}:{m or '00'}"


class Triggers:
    def __init__(self, state: RuntimeState) -> None:
        self._s = state

    def all(self) -> list[dict[str, Any]]:
        try:
            return list(json.loads(self._s.get("triggers", "[]")))
        except ValueError:
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self._s.set("triggers", json.dumps(items))

    def add(self, trig: dict[str, Any]) -> dict[str, Any]:
        items = self.all()
        trig = {"id": max([int(i["id"]) for i in items] + [0]) + 1, "created": get_clock().now_ms(), **trig}
        self._save(items + [trig])
        return trig

    def remove(self, tid: int | None) -> int:
        items = self.all()
        keep = [i for i in items if tid is not None and int(i["id"]) != tid] if tid is not None else []
        self._save(keep)
        return len(items) - len(keep)

    def describe(self, t: dict[str, Any]) -> str:
        if t["kind"] == "cron":
            when = "every " + (t["day"] if t["day"] != "daily" else "day") + f" at {t['at']}"
            return f"#{t['id']} {when}: {t['command']}"
        act = t.get("command") or "tell me"
        return f"#{t['id']} when a {t['channel']} from '{t['match']}' arrives: {act}"

    def parse(self, text: str) -> str | None:
        """Handles trigger commands typed in chat. Returns the reply, or None if the text is not about triggers."""
        if LIST.search(text):
            items = self.all()
            return "Your triggers:\n" + "\n".join(self.describe(i) for i in items) if items else "You have no triggers yet. Try: every day at 8am catch me up on my emails."
        m = STOP.search(text)
        if m and re.search(r"\b(?:trigger|automation|schedule|rule)", text, re.I) and not text.lower().startswith(("every", "when")):
            n = self.remove(int(m.group(1))) if m.group(1) else self.remove(None)
            return f"Removed {n} trigger(s)." if n else "I did not find that trigger. Say 'list my triggers' to see their numbers."
        c = CRON.match(text)
        if c:
            day = c.group(1).lower().replace("each ", "every ").replace("every ", "").strip()
            day = "daily" if day in ("day", "daily", "morning", "evening", "night") else day
            at = _hhmm(c.group(2), c.group(3), c.group(4))
            cmd = c.group(5).strip().rstrip(".")
            if len(cmd) < 3:
                return None
            t = self.add({"kind": "cron", "day": day, "at": at, "command": cmd, "last_run": ""})
            return f"Okay. {self.describe(t)}. Anything it wants to send or change will still wait for your approval."
        w = WHEN_MSG.match(text)
        if w:
            chan = {"email": "gmail", "mail": "gmail", "message": "any", "dm": "any"}[w.group(1).lower()]
            rest = w.group(2).strip().rstrip(".")
            # 'Dev, tell me' / 'Priya then catch me up on Orion': the first separator ends the sender, what follows is a command or just 'tell me'
            parts = re.split(r"\s*[,;]\s*|\s+(?:then|and then)\s+", rest, maxsplit=1)
            who = parts[0].strip(" '\"").lower()
            cmd = parts[1].strip() if len(parts) > 1 else ""
            if re.match(r"(?:please\s+)?(?:tell|alert|notify|let|ping)\s+me\b", cmd, re.I):
                cmd = ""
            t = self.add({"kind": "arrival", "channel": chan, "match": who, "command": cmd})
            return f"Okay. {self.describe(t)}."
        return None

    def due_cron(self, now: datetime) -> list[dict[str, Any]]:
        out, items, changed = [], self.all(), False
        for t in items:
            if t["kind"] != "cron":
                continue
            if t["day"] not in ("daily", "weekday") and DAYS[now.weekday()] != t["day"]:
                continue
            if t["day"] == "weekday" and now.weekday() >= 5:
                continue
            today = now.strftime("%Y-%m-%d")
            if t.get("last_run") != today and now.strftime("%H:%M") >= t["at"]:
                t["last_run"], changed = today, True
                out.append(dict(t))
        if changed:
            self._save(items)
        return out


# ------------------------------------------------------------------ the engine
class Proactive:
    def __init__(self, state: RuntimeState, reader: MemoryReader, *, run_command: Runner | None = None, agenda: Callable[[], Awaitable[str]] | None = None) -> None:
        self._s, self._r = state, reader
        self.inbox, self.triggers = Inbox(state), Triggers(state)
        self._run, self._agenda = run_command, agenda

    # settings -------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        return {"briefing_at": self._s.get("briefing_at", "08:00") or None, "os_notify": self._s.get("os_notify", "0") == "1", "enabled": self._s.get("proactive", "1") == "1"}

    def configure(self, *, briefing_at: str | None | bool = False, os_notify: bool | None = None, enabled: bool | None = None) -> None:
        if briefing_at is not False:
            self._s.set("briefing_at", briefing_at or "")
        if os_notify is not None:
            self._s.set("os_notify", "1" if os_notify else "0")
        if enabled is not None:
            self._s.set("proactive", "1" if enabled else "0")

    def _push(self, kind: str, title: str, body: str, key: str, source: str = "", link: str | None = None) -> None:
        self.inbox.add(kind, title, body, key=key, source=source, link=link, os_notify=self.settings()["os_notify"])

    # one pass -------------------------------------------------------
    async def run(self) -> int:
        if not self.settings()["enabled"]:
            return 0
        before = len(self.inbox.items())
        now_ms = get_clock().now_ms()
        now = datetime.fromtimestamp(now_ms / 1000)
        self._arrivals(now_ms)
        self._upcoming(now_ms)
        await self._briefing(now)
        for t in self.triggers.due_cron(now):
            await self._fire(t, f"cron:{t['id']}:{now.strftime('%Y-%m-%d')}")
        return max(0, len(self.inbox.items()) - before)

    def _arrivals(self, now_ms: int) -> None:
        cursor = int(self._s.get("pro_cursor", "0") or 0)
        if cursor == 0:  # first start: look only forward, never flood with history
            self._s.set("pro_cursor", str(now_ms))
            return
        rows = self._r.items_since(cursor)
        if rows:
            self._s.set("pro_cursor", str(max(r["ingested_at"] for r in rows)))
        flagged = 0
        for r in rows:
            if r["source"] not in CHANNELS or r["trust"] not in ("received", "web"):
                continue
            text = f"{r['title'] or ''} {r['preview'] or ''}"
            who = r["author"] or r["from_addr"] or r["title"] or r["source"]
            if scan(r["preview"] or "", "prompt").injection_suspected:
                self._push("alert", f"Suspicious message from {who}", f"A {r['source']} message tries to give me instructions. I ignored it and did nothing.", f"inj:{r['id']}", r["source"], r["deep_link"])
                continue
            for t in self.triggers.all():
                if t["kind"] == "arrival" and t["channel"] in ("any", r["source"]) and t["match"] in f"{who} {r['from_addr']} {r['title']}".lower():
                    self._push("alert", f"{who} ({r['source']})", (r["preview"] or "")[:200], f"trig:{t['id']}:{r['id']}", r["source"], r["deep_link"])
                    if t.get("command"):
                        self._schedule(t["command"], f"trigcmd:{t['id']}:{r['id']}")
            if flagged < 5 and needs_attention(r["title"] or "", r["preview"] or "") and f"{text}".strip():
                flagged += 1
                self._push("attention", f"Needs your attention: {who}", f"{r['title'] or r['source']}: {(r['preview'] or '')[:160]}", f"att:{r['id']}", r["source"], r["deep_link"])

    def _upcoming(self, now_ms: int) -> None:
        for r in self._r.items_between(("gcal", "outlook_cal"), now_ms, now_ms + LEAD_MIN * 60_000):
            mins = max(0, round((r["ts"] - now_ms) / 60_000))
            self._push("heads_up", f"In {mins} min: {r['title']}", datetime.fromtimestamp(r["ts"] / 1000).strftime("%H:%M") + f" {r['title']}", f"evt:{r['title']}:{r['ts']}", "gcal", r["deep_link"])

    async def _briefing(self, now: datetime) -> None:
        at = self.settings()["briefing_at"]
        if not at or now.strftime("%H:%M") < at:
            return
        key = f"brief:{now.strftime('%Y-%m-%d')}"
        if self.inbox.seen(key):
            return
        lines = []
        if self._agenda:
            lines.append(await self._agenda())
        since = int((now - timedelta(hours=18)).timestamp() * 1000)
        rows = [r for r in self._r.items_since(since, limit=300) if r["source"] in CHANNELS]
        if rows:
            by = {}
            for r in rows:
                by[r["source"]] = by.get(r["source"], 0) + 1
            lines.append("New since yesterday: " + ", ".join(f"{n} {s}" for s, n in sorted(by.items())) + ".")
            need = [r for r in rows if r["trust"] == "received" and needs_attention(r["title"] or "", r["preview"] or "")][:3]
            if need:
                lines.append("Needs you: " + "; ".join(f"{r['author'] or r['source']}: {(r['title'] or r['preview'] or '')[:50]}" for r in need))
        self._push("briefing", "Good morning: your briefing", "\n".join(lines) or "Nothing new overnight.", key)

    def _schedule(self, command: str, key: str) -> None:
        if self._run is None or self.inbox.seen(key):
            return
        self.inbox.add("scheduled", f"Running: {command[:60]}", "Started by one of your triggers.", key=key)
        import asyncio

        asyncio.get_running_loop().create_task(self._execute(command))

    async def _fire(self, trig: dict[str, Any], key: str) -> None:
        if self._run is None:
            return
        self.inbox.add("scheduled", f"Scheduled: {trig['command'][:60]}", "Running now.", key=key)
        import asyncio

        asyncio.get_running_loop().create_task(self._execute(trig["command"]))

    async def _execute(self, command: str) -> None:
        try:
            reply = await self._run(command) if self._run else ""
        except Exception as exc:  # noqa: BLE001 - a failing trigger is reported, never fatal
            log("trigger_failed", err=type(exc).__name__)
            reply = f"It failed ({type(exc).__name__})."
        self.inbox.add("scheduled", f"Result: {command[:60]}", reply[:600], key=f"res:{new_id()}")
