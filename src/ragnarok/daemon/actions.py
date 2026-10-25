"""The first-party action handlers (native skills). They run inside the daemon, but only ever behind guard + broker: the executor calls them
after policy allowed and, for tier 2+, after a human approved this exact content. `dry_run=True` never changes anything and shows what would happen."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ragnarok.connectors.discord import Discord
from ragnarok.connectors.google import Gmail, GoogleAuth, valid_address
from ragnarok.connectors.slack import Slack
from ragnarok.connectors.telegram import Telegram
from ragnarok.core.clock import get_clock
from ragnarok.core.errors import RagnarokError
from ragnarok.harness.executor import ExecContext, NativeHandler
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

    # ------------------------------------------------------------------ reads and local writes
    async def memory_search(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        res = await retrieve(p["query"], compartment=self._comp, reader=self._r, index=self._i, gateway=self._gw, now_ms=get_clock().now_ms(),
                             deadline=get_clock().monotonic() + 3, k=5, token_budget=700)
        return {"hits": [{"text": h.text[:300], "citation": h.citation, "trust": h.trust} for h in res.hits]}

    async def reminder_add(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        due = parse_due(p.get("when", ""), p["text"])
        shown = datetime.fromtimestamp(due / 1000).strftime("%a %d %b, %H:%M") if due else "no time set"
        if dry_run:
            return {"would_remind": p["text"], "when": shown}
        rid = self._c.add(self._comp, p["text"], due)
        return {"reminder_id": rid, "text": p["text"], "when": shown, "due_at": due}

    async def reminder_remove(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        self._c.remove(p["reminder_id"])
        return {"removed": True}

    async def reminder_list(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        return {"reminders": [{"text": r["what"], "due_at": r["due_at"]} for r in self._c.list_open()]}

    async def mail_draft(self, p: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]:
        draft = {"to": p["to"], "subject": p.get("subject", ""), "body": p["body"]}
        if dry_run:
            return {"would_draft": draft}
        self._m.write_event(EventIn(compartment=self._comp, kind="draft", actor="agent", content=json.dumps(draft), trust="agent"))
        return {"draft": draft}

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

    def natives(self) -> dict[str, NativeHandler]:
        return {"memory.search": self.memory_search, "reminder.add": self.reminder_add, "reminder.list": self.reminder_list, "mail.draft": self.mail_draft,
                "mail.send": self.mail_send, "chat.reply": self.chat_reply}
