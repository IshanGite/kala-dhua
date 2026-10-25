"""Discord bot, read side, through the REST API (no gateway needed for reading history).

Prior work: channel/message handling adapted from groupify. Official-docs notes (verify live): GET /users/@me/guilds, GET /guilds/{id}/channels
(type 0 = text), GET /channels/{id}/messages?after=<snowflake>&limit=100; the bot needs View Channel + Read Message History and the privileged
Message Content intent enabled in the developer portal, otherwise content comes back empty. Snowflakes encode time: (ms - 1420070400000) << 22.
"""

from __future__ import annotations

from typing import Any

import httpx

from ragnarok.connectors.base import ActionSpec, ConnectorInfo, Cursor, Health, ItemRef, RawItem, SyncResult
from ragnarok.connectors.http import ConnectorError, new_client, request
from ragnarok.core.clock import get_clock

API = "https://discord.com/api/v10"
EPOCH = 1_420_070_400_000


def snowflake_at(ms: int) -> str:
    return str((ms - EPOCH) << 22)


class Discord:
    name = "discord"

    def __init__(self, bot_token: str, *, client: httpx.AsyncClient | None = None, backfill_days: int = 7) -> None:
        self._h = {"Authorization": f"Bot {bot_token}"}
        self._http, self._days = client or new_client(), backfill_days

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo("discord", "Discord", ("message",), domains=("discord.com",), default_minimisation="text channels the bot can see; 7 days backfill")

    async def _get(self, path: str, params: dict[str, Any] | None = None, *, soft: bool = False) -> Any:
        try:
            r = await request(self._http, "GET", f"{API}{path}", headers=self._h, params=params)
        except ConnectorError as exc:
            if soft and exc.code == "E_AUTH":
                return None  # a channel the bot may not read is skipped, not fatal
            raise
        if r.status_code >= 400:
            raise ConnectorError(f"discord {r.status_code}", code="E_DISCORD")
        return r.json()

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        marks: dict[str, str] = dict((cursor.value if cursor else {}).get("after", {}))
        start = snowflake_at(get_clock().now_ms() - self._days * 86_400_000)
        refs: list[ItemRef] = []
        for g in await self._get("/users/@me/guilds") or []:
            for ch in await self._get(f"/guilds/{g['id']}/channels", soft=True) or []:
                if ch.get("type") != 0:
                    continue
                cid = ch["id"]
                after = marks.get(cid, start)
                for _ in range(20):
                    page = await self._get(f"/channels/{cid}/messages", {"after": after, "limit": 100}, soft=True)
                    if not page:
                        break
                    page.sort(key=lambda m: int(m["id"]))
                    for m in page:
                        after = m["id"]
                        a = m.get("author") or {}
                        text = (m.get("content") or "").strip()
                        if not text or a.get("bot"):
                            continue
                        refs.append(ItemRef(f"{cid}:{m['id']}", {"text": text, "name": a.get("global_name") or a.get("username"), "sender": a.get("id"), "ch": ch.get("name"), "guild": g.get("name"), "ts": m.get("timestamp"), "gid": g["id"]}, priority=1 if cid in marks else 5))
                    if len(page) < 100:
                        break
                marks[cid] = after
        return SyncResult(refs, Cursor({"after": marks}))

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        h = ref.hint
        if "text" not in h:
            return None
        cid, mid = ref.native_id.split(":", 1)
        ms = (int(mid) >> 22) + EPOCH
        return RawItem(
            source=self.name, native_id=ref.native_id, text=h["text"], trust="received", title=f"{h.get('guild', '')} #{h.get('ch', cid)}".strip(),
            visibility="public", occurred_at=ms, author=h.get("name"), deep_link=f"https://discord.com/channels/{h.get('gid')}/{cid}/{mid}" if h.get("gid") else None,
        )

    def actions(self) -> list[ActionSpec]:
        return [ActionSpec("chat.post", "Post a Discord message", {"channel_id": "string", "text": "string"}, tier=2)]

    async def post(self, write_token: str, channel_id: str, text: str) -> str:
        r = await request(self._http, "POST", f"{API}/channels/{channel_id}/messages", headers={"Authorization": f"Bot {write_token}"}, json={"content": text[:2000], "allowed_mentions": {"parse": []}})
        if r.status_code >= 300:
            raise ConnectorError(f"discord post {r.status_code}", code="E_DISCORD")
        return str(r.json()["id"])

    async def health(self) -> Health:
        try:
            me = await self._get("/users/@me")
        except ConnectorError as exc:
            return Health(False, exc.code)
        return Health(bool(me), (me or {}).get("username", ""))
