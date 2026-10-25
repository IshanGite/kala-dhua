"""The first-party action handlers (native skills). They run inside the daemon, but only ever behind guard + broker: the executor calls them
after policy allowed and, for tier 2+, after a human approved this exact content. `dry_run=True` never changes anything and shows what would happen."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ragnarok.connectors.discord import Discord
from ragnarok.connectors.google import Calendar, Drive, Gmail, GoogleAuth, valid_address
from ragnarok.connectors.slack import Slack
from ragnarok.connectors.telegram import Telegram
from ragnarok.core.clock import get_clock
from ragnarok.core.errors import RagnarokError
from ragnarok.harness.executor import ExecContext, NativeHandler
from ragnarok.ingest.pipeline import ingest_text
from ragnarok.llm_gateway.client import Gateway
from ragnarok.memory.commitments import Commitments
from ragnarok.memory.models import EventIn
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.retrieval import retrieve
from ragnarok.memory.service import MemoryService
from ragnarok.platform.secrets import SecretStore, token_name
from ragnarok.router.when import parse_when
from ragnarok.store.vectors import VectorIndex


class ActionError(RagnarokError):
    pass


def parse_due(*phrases: str) -> int | None:
    """The first phrase that yields a time, by deterministic code (never the model's date arithmetic); None means 'no time', not a guess."""
    now = datetime.fromtimestamp(get_clock().now_ms() / 1000)
    for ph in phrases:
        dt = parse_when(ph or "", now)
        if dt is not None:
            return int(dt.astimezone().timestamp() * 1000)
    return None


class Actions:
    def __init__(self, mem: MemoryService, reader: MemoryReader, index: VectorIndex, gateway: Gateway, vault: SecretStore, compartment: str = "work", *,
                 senders: dict[str, Callable[..., Any]] | None = None) -> None:
        self._m, self._r, self._i, self._gw, self._vault, self._comp = mem, reader, index, gateway, vault, compartment
        self._c = Commitments(mem)
        self._senders = senders  # tests inject fakes; None means the real connectors
        self._wauth: GoogleAuth | None = None
        self._wraw = ""

    # ------------------------------------------------------------------ reads and local writes
    async def memory_search(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        res = await retrieve(p["query"], compartment=self._comp, reader=self._r, index=self._i, gateway=self._gw, now_ms=get_clock().now_ms(),
                             deadline=get_clock().monotonic() + 3, k=5, token_budget=700)
        hits = [{"text": h.text[:300], "citation": h.citation, "trust": h.trust} for h in res.hits]
        return {"hits": hits, "first": hits[0]["text"][:200] if hits else "", "summary": " | ".join(h["text"][:160] for h in hits[:4])}

    async def calendar_agenda(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        from ragnarok.router.when import agenda_range

        a0, a1 = agenda_range(p.get("when", "") or "today", datetime.fromtimestamp(get_clock().now_ms() / 1000))
        rows = self._r.items_between(("gcal", "outlook_cal"), int(a0.timestamp() * 1000), int(a1.timestamp() * 1000))
        lines = [f"{datetime.fromtimestamp((r['ts'] or 0) / 1000).strftime('%H:%M')} {r['title'] or '(no title)'}" for r in rows]
        return {"events": lines, "first": lines[0] if lines else "", "summary": ("Calendar for " + a0.strftime("%a %d %b") + ": " + "; ".join(lines)) if lines else f"Nothing on the calendar for {a0.strftime('%a %d %b')}."}

    async def reminder_add(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        due = parse_due(p.get("when", ""), p["text"])
        shown = datetime.fromtimestamp(due / 1000).strftime("%a %d %b, %H:%M") if due else "no time set"
        if dry_run:
            return {"would_remind": p["text"], "when": shown}
        rid = self._c.add(self._comp, p["text"], due)
        past = bool(due and due <= get_clock().now_ms())
        return {"reminder_id": rid, "text": p["text"], "when": shown, "due_at": due, "past": past}

    async def reminder_remove(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        self._c.remove(p["reminder_id"])
        return {"removed": True}

    async def reminder_list(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        return {"reminders": [{"text": r["what"], "due_at": r["due_at"]} for r in self._c.list_open()]}

    async def mail_draft(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        draft = {"to": p["to"], "subject": p.get("subject", ""), "body": p["body"]}
        if dry_run:
            return {"would_draft": draft}
        auth = self._write_auth()
        if self._senders and "gmail_draft" in self._senders:
            return {"draft": draft, "in_gmail": True, "id": await self._senders["gmail_draft"](draft)}
        if auth is not None:  # a real draft in Gmail, where the user will see it with their own mail
            did = await Gmail(auth).create_draft(auth, p["to"], draft["subject"], p["body"])
            return {"draft": draft, "in_gmail": True, "id": did}
        self._m.write_event(EventIn(compartment=self._comp, kind="draft", actor="agent", content=json.dumps(draft), trust="agent"))
        return {"draft": draft}

    async def _remember_write(self, source: str, native_id: str, text: str, title: str, kind: str, occurred_at: int | None, link: str) -> None:
        """Things the agent creates are in its own memory at once, not after the next poll; a later poll of the same id just refreshes it."""
        try:
            await ingest_text(self._m, self._gw, self._i, source=source, native_id=native_id or title, compartment=self._comp, trust="user_file", text=text, title=title, kind=kind,
                              occurred_at=occurred_at, deep_link=link if link.startswith("https://") else None)
        except Exception as exc:  # noqa: BLE001 - the write already happened; failing to index it must not look like a failed write
            from ragnarok.core.logging import log

            log("write_through_failed", err=type(exc).__name__)

    async def my_address(self) -> str:
        """The signed-in Gmail address (for 'send it to myself' showcase actions). Demo and test senders use a placeholder; nothing real is contacted for them."""
        if getattr(self, "_me", ""):
            return self._me  # type: ignore[no-any-return]
        if self._senders and "mail" in self._senders:
            return "you@demo.example"
        raw = self._vault.get(token_name("google", "read")) or self._vault.get(token_name("google", "write"))
        if raw is None:
            return ""
        g = json.loads(raw)
        try:
            self._me = str((await GoogleAuth(g["client_id"], g["client_secret"], g["refresh_token"]).get(f"{Gmail.API}/profile")).json().get("emailAddress", ""))
        except Exception:  # noqa: BLE001
            self._me = ""
        return self._me

    def _write_auth(self) -> GoogleAuth | None:
        raw = self._vault.get(token_name("google", "write"))
        if raw is None:
            return None
        if self._wauth is None or self._wraw != raw:  # one object keeps its short-lived access token, so each action skips a sign-in round trip
            g = json.loads(raw)
            self._wauth, self._wraw = GoogleAuth(g["client_id"], g["client_secret"], g["refresh_token"]), raw
        return self._wauth

    async def calendar_add(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        due = parse_due(p["when"])
        if due is None:
            raise ActionError("I could not tell what time that is; say it like 'tomorrow at 3pm'", code="E_NO_TIME")
        shown = datetime.fromtimestamp(due / 1000).strftime("%a %d %b, %H:%M")
        if dry_run:
            return {"would_add": {"title": p["title"], "when": shown, "minutes": p.get("minutes", 30)}}
        if self._senders and "calendar" in self._senders:
            link = await self._senders["calendar"](p, due)
            await self._remember_write("gcal", f"test-{abs(hash((p['title'], due)))}", f"Event: {p['title']}\nWhen: {shown}", p["title"], "event", due, link)
            return {"added": True, "title": p["title"], "when": shown, "link": link}
        auth = self._write_auth()
        if auth is None:
            raise ActionError("adding events needs the write sign-in: ragnarok connect google --write", code="E_NOT_CONNECTED")
        eid, _, link = (await Calendar(auth).insert_event(auth, p["title"], datetime.fromtimestamp(due / 1000), int(p.get("minutes", 30)))).partition("|")
        await self._remember_write("gcal", eid, f"Event: {p['title']}\nWhen: {shown}", p["title"], "event", due, link)
        return {"added": True, "title": p["title"], "when": shown, "link": link}

    # ------------------------------------------------------------------ external writes (always behind an approval card)
    async def mail_send(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        if not valid_address(p["to"]):
            raise ActionError("not a valid recipient address", code="E_BAD_RECIPIENT")
        if dry_run:
            return {"would_send": {"to": p["to"], "subject": p["subject"], "body": p["body"]}}
        if self._senders and "mail" in self._senders:
            return {"sent": True, "id": await self._senders["mail"](p)}
        raw = self._vault.get(token_name("google", "write"))
        if raw is None:
            raise ActionError("sending mail needs the write sign-in: ragnarok connect google --write", code="E_NOT_CONNECTED")
        g = json.loads(raw)
        mid = await Gmail(GoogleAuth(g["client_id"], g["client_secret"], g["refresh_token"])).send(GoogleAuth(g["client_id"], g["client_secret"], g["refresh_token"]), p["to"], p["subject"], p["body"])
        return {"sent": True, "id": mid}

    def _resolve(self, platform: str, target: str) -> tuple[str, str]:
        conv = self._r.find_conversation(platform, target)
        if conv is None:
            raise ActionError(f"no {platform} conversation named {target!r} is known yet", code="E_UNKNOWN_TARGET")
        return conv["native_id"].split(":", 1)[0], str(conv.get("title") or target)

    async def chat_reply(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        chat_id, title = self._resolve(p["platform"], p["target"])
        if dry_run:
            return {"would_post": {"platform": p["platform"], "to": title, "text": p["text"]}}
        if self._senders and p["platform"] in self._senders:
            return {"posted": True, "to": title, "id": await self._senders[p["platform"]](chat_id, p["text"])}
        tok = self._vault.get(token_name(p["platform"], "write")) or self._vault.get(token_name(p["platform"], "read"))
        if tok is None:
            raise ActionError(f"{p['platform']} is not connected", code="E_NOT_CONNECTED")
        if p["platform"] == "telegram":
            mid = await Telegram(tok).post(tok, chat_id, p["text"])
        elif p["platform"] == "discord":
            mid = await Discord(tok).post(tok, chat_id, p["text"])
        else:
            mid = await Slack(tok).post(tok, chat_id, p["text"])
        return {"posted": True, "to": title, "id": mid}

    async def mail_reply(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        m = self._r.find_mail("gmail", p["target"])
        if m is None:
            raise ActionError(f"no email from {p['target']!r} is known yet", code="E_UNKNOWN_TARGET")
        to = str(m["flags"].get("from", ""))
        if not valid_address(to):
            raise ActionError("that message has no usable sender address", code="E_BAD_RECIPIENT")
        title = str(m["title"] or "")
        subject = title if title.lower().startswith("re:") else f"Re: {title}"
        if dry_run:
            return {"would_reply": {"to": to, "subject": subject, "body": p["body"], "in_thread": bool(m["thread_id"])}}
        if self._senders and "mail_reply" in self._senders:
            return {"sent": True, "to": to, "id": await self._senders["mail_reply"](to, subject, p["body"], m)}
        auth = self._write_auth()
        if auth is None:
            raise ActionError("replying needs the write sign-in: ragnarok connect google --write", code="E_NOT_CONNECTED")
        mid = await Gmail(auth).send(auth, to, subject, p["body"], thread_id=str(m["thread_id"] or ""), in_reply_to=str(m["flags"].get("message_id", "")), references=str(m["flags"].get("references", "")))
        return {"sent": True, "to": to, "id": mid}

    async def drive_create(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {"would_create": {"title": p["title"], "chars": len(p["text"])}}
        if self._senders and "drive" in self._senders:
            link = await self._senders["drive"](p)
            await self._remember_write("gdrive", f"test-{abs(hash(p['title']))}", p["text"], p["title"], "file", None, link)
            return {"created": True, "title": p["title"], "link": link}
        auth = self._write_auth()
        if auth is None:
            raise ActionError("creating documents needs the write sign-in: ragnarok connect google --write", code="E_NOT_CONNECTED")
        did, _, link = (await Drive(auth).create_doc(auth, p["title"], p["text"])).partition("|")
        await self._remember_write("gdrive", did, p["text"], p["title"], "file", None, link)
        return {"created": True, "title": p["title"], "link": link}

    def natives(self) -> dict[str, NativeHandler]:
        return {"memory.search": self.memory_search, "calendar.agenda": self.calendar_agenda, "reminder.add": self.reminder_add, "reminder.list": self.reminder_list, "mail.draft": self.mail_draft,
                "mail.send": self.mail_send, "chat.reply": self.chat_reply, "calendar.add": self.calendar_add, "mail.reply": self.mail_reply, "drive.create": self.drive_create}
