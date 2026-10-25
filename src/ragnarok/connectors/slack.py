"""Slack, read side, by polling conversations.history for every conversation the bot belongs to.

Prior work: message cleaning and event filtering adapted from groupify (github.com/abhi-dev99/groupify).
Official-docs notes (verify live): methods users.conversations, conversations.history (oldest, cursor, limit), chat.postMessage; read scopes
channels:history, groups:history, im:history, mpim:history, channels:read, groups:read, im:read, mpim:read, users:read; chat:write for the write
token (separate item). Rate limits are tiered; a 429 carries Retry-After and http.request honours it. Socket Mode (live push) is a later add-on
that feeds the same queue; polling is the fallback that always works.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any

import httpx

from ragnarok.connectors.base import ActionSpec, ConnectorInfo, Cursor, Health, ItemRef, RawItem, SyncResult
from ragnarok.connectors.http import ConnectorError, new_client, request
from ragnarok.core.clock import get_clock

API = "https://slack.com/api"
PAGE = 200


def clean_text(raw: str) -> str:
    t = raw or ""
    t = re.sub(r"<@([UW][A-Z0-9]+)(?:\|([^>]+))?>", lambda m: "@" + (m.group(2) or m.group(1)), t)
    t = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", t)
    t = re.sub(r"<!(here|channel|everyone)(?:\|[^>]*)?>", r"@\1", t)
    t = re.sub(r"<!subteam\^[A-Z0-9]+\|?([^>]*)>", lambda m: m.group(1) or "@group", t)
    t = re.sub(r"<(?:mailto:|https?://)[^>|]+\|([^>]+)>", r"\1", t)
    t = re.sub(r"<((?:mailto:)?(?:https?://)?[^>|]+)>", r"\1", t)
    return unescape(t).strip()


class Slack:
    name = "slack"

    def __init__(self, bot_token: str, *, client: httpx.AsyncClient | None = None, backfill_days: int = 14, team_id: str | None = None) -> None:
        self._tok, self._http, self._days, self._team = bot_token, client or new_client(), backfill_days, team_id
        self._names: dict[str, str | None] = {}

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo(
            "slack", "Slack", ("message",),
            read_scopes=("channels:history", "groups:history", "im:history", "mpim:history", "channels:read", "groups:read", "im:read", "mpim:read", "users:read"),
            write_scopes=("chat:write",), domains=("slack.com",), default_minimisation="messages only; no files; 14 days backfill",
        )

    async def _call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        r = await request(self._http, "POST", f"{API}/{method}", headers={"Authorization": f"Bearer {self._tok}"}, data=body)
        data = r.json()
        if not data.get("ok"):
            err = str(data.get("error", r.status_code))
            raise ConnectorError(f"slack {method}: {err}", code="E_AUTH" if err in ("invalid_auth", "token_revoked", "not_authed") else "E_SLACK")
        return data

    async def _conversations(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(20):
            d = await self._call("users.conversations", {"types": "public_channel,private_channel,mpim,im", "limit": 200, "exclude_archived": "true", **({"cursor": cursor} if cursor else {})})
            out += d.get("channels", [])
            cursor = (d.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                break
        return out

    @staticmethod
    def _visibility(ch: dict[str, Any]) -> str:
        if ch.get("is_im"):
            return "dm"
        return "private" if (ch.get("is_private") or ch.get("is_mpim")) else "public"

    async def _display(self, user: str) -> str | None:
        if user not in self._names:
            try:
                u = (await self._call("users.info", {"user": user})).get("user", {})
                self._names[user] = (u.get("profile") or {}).get("display_name") or u.get("real_name") or u.get("name")
            except ConnectorError:
                self._names[user] = None
        return self._names[user]

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        marks: dict[str, str] = dict((cursor.value if cursor else {}).get("ts", {}))
        first_oldest = f"{(get_clock().now_ms() - self._days * 86_400_000) / 1000:.6f}"
        refs: list[ItemRef] = []
        for ch in await self._conversations():
            cid = ch["id"]
            oldest = marks.get(cid, first_oldest)
            newest = oldest
            token = ""
            for _ in range(50):
                d = await self._call("conversations.history", {"channel": cid, "oldest": oldest, "limit": PAGE, **({"cursor": token} if token else {})})
                for m in d.get("messages", []):
                    ts = m.get("ts", "")
                    newest = max(newest, ts)
                    if m.get("bot_id") or not m.get("user") or (m.get("subtype") and m["subtype"] not in ("thread_broadcast", "file_share")):
                        continue
                    text = clean_text(m.get("text", ""))
                    if not text:
                        continue
                    refs.append(ItemRef(f"{cid}:{ts}", {"text": text, "user": m["user"], "thread": m.get("thread_ts"), "vis": self._visibility(ch), "ch": ch.get("name") or cid}, priority=1 if cid in marks else 5))
                token = (d.get("response_metadata") or {}).get("next_cursor", "")
                if not d.get("has_more") or not token:
                    break
            marks[cid] = newest
        return SyncResult(refs, Cursor({"ts": marks}))

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        h = ref.hint
        if "text" not in h:
            return None
        cid, ts = ref.native_id.split(":", 1)
        who = await self._display(h["user"])
        return RawItem(
            source=self.name, native_id=ref.native_id, text=h["text"], trust="received", title=f"#{h.get('ch', cid)}", visibility=h.get("vis", "private"),
            thread_id=h.get("thread"), occurred_at=int(float(ts) * 1000), author=who or h["user"], deep_link=None,
        )

    def actions(self) -> list[ActionSpec]:
        return [ActionSpec("chat.post", "Post a message to a Slack channel or DM", {"channel": "string", "text": "string", "thread_ts": "string?"}, tier=2)]

    async def post(self, write_token: str, channel: str, text: str, thread_ts: str | None = None) -> str:
        r = await request(self._http, "POST", f"{API}/chat.postMessage", headers={"Authorization": f"Bearer {write_token}"},
                          data={"channel": channel, "text": text, "unfurl_links": "false", **({"thread_ts": thread_ts} if thread_ts else {})})
        d = r.json()
        if not d.get("ok"):
            raise ConnectorError(f"slack post: {d.get('error')}", code="E_SLACK")
        return str(d["ts"])

    async def health(self) -> Health:
        try:
            d = await self._call("auth.test", {})
        except ConnectorError as exc:
            return Health(False, exc.code)
        return Health(True, f"team {d.get('team_id', '')[:3]}…")
