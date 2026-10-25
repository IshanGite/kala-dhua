"""Telegram bot, read side, with getUpdates long-polling disabled (timeout 0) so a sync never blocks.

Prior work: update parsing adapted from groupify. Official-docs notes (verify live): getUpdates(offset, limit, allowed_updates) confirms updates by
offset; Telegram keeps unconfirmed updates for 24 h; bots only see messages sent to them or posted where they are a member (privacy mode applies in groups);
sendMessage for the write token. The offset advances only after the refs are queued and each ref carries its message, so a crash loses nothing.
"""

from __future__ import annotations

from typing import Any

import httpx

from ragnarok.connectors.base import ActionSpec, ConnectorInfo, Cursor, Health, ItemRef, RawItem, SyncResult
from ragnarok.connectors.http import ConnectorError, new_client, request

API = "https://api.telegram.org"


class Telegram:
    name = "telegram"

    def __init__(self, bot_token: str, *, client: httpx.AsyncClient | None = None, long_poll_s: int = 0) -> None:
        self._tok, self._http = bot_token, client or new_client()
        self._lp = max(0, min(long_poll_s, 50))  # >0: getUpdates waits up to this long for a message and returns the moment one arrives

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo("telegram", "Telegram", ("message",), domains=("api.telegram.org",), default_minimisation="text only; unknown senders held until paired")

    async def _call(self, method: str, body: dict[str, Any], token: str | None = None) -> Any:
        r = await request(self._http, "POST", f"{API}/bot{token or self._tok}/{method}", json=body, timeout=httpx.Timeout(self._lp + 15.0, connect=10.0))
        d = r.json()
        if not d.get("ok"):
            raise ConnectorError(f"telegram {method}: {d.get('error_code')}", code="E_AUTH" if d.get("error_code") in (401, 403) else "E_TELEGRAM")
        return d["result"]

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        offset = int((cursor.value if cursor else {}).get("offset", 0))
        refs: list[ItemRef] = []
        nxt = offset
        more = False
        updates = await self._call("getUpdates", {"offset": offset, "limit": 100, "timeout": self._lp, "allowed_updates": ["message", "channel_post"]})
        for u in updates:
            nxt = max(nxt, u["update_id"] + 1)
            m = u.get("message") or u.get("channel_post")
            if not m:
                continue
            text = m.get("text") or m.get("caption") or ""
            frm = m.get("from") or {}
            if not text or frm.get("is_bot"):
                continue
            chat = m["chat"]
            sender = str(frm.get("id", chat["id"]))
            vis = "dm" if chat.get("type") == "private" else "private"
            name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or frm.get("username") or sender
            refs.append(ItemRef(f"{chat['id']}:{m['message_id']}", {"text": text, "sender": sender, "name": name, "vis": vis, "date": m.get("date"), "title": chat.get("title") or name}, priority=1))
        more = len(updates) >= 100
        return SyncResult(refs, Cursor({"offset": nxt}), more=more)

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        h = ref.hint
        if "text" not in h:
            return None
        return RawItem(
            source=self.name, native_id=ref.native_id, text=h["text"], trust="received", title=h.get("title"), visibility=h.get("vis", "private"),
            occurred_at=int(h["date"]) * 1000 if h.get("date") else None, author=h.get("name"),
        )

    def actions(self) -> list[ActionSpec]:
        return [ActionSpec("chat.post", "Send a Telegram message", {"chat_id": "string", "text": "string"}, tier=2)]

    async def post(self, write_token: str, chat_id: str, text: str) -> str:
        res = await self._call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, token=write_token)
        return str(res["message_id"])

    async def health(self) -> Health:
        try:
            me = await self._call("getMe", {})
        except ConnectorError as exc:
            return Health(False, exc.code)
        return Health(True, f"@{me.get('username', '?')}")
