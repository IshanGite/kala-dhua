"""Outlook mail and calendar over Microsoft Graph, read-only.

Prior work: the Graph mail approach (plain-text body preference, immutable ids) follows groupify. Official-docs notes (verify live): token endpoint
https://login.microsoftonline.com/common/oauth2/v2.0/token (grant_type=refresh_token, public client, no secret); delegated scopes Mail.Read,
Calendars.Read, offline_access (Mail.Send is a separate write token); GET /me/mailFolders/inbox/messages with $filter=receivedDateTime ge <iso>,
$orderby, $select and Prefer: outlook.body-content-type="text", IdType="ImmutableId"; GET /me/calendarView?startDateTime&endDateTime.
Device-code sign-in (`ragnarok connect outlook`) needs no redirect URI and no local web server, which is why it is used.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ragnarok.connectors.base import ActionSpec, ConnectorInfo, Cursor, Health, ItemRef, RawItem, SyncResult
from ragnarok.connectors.http import ConnectorError, new_client, request
from ragnarok.core.clock import get_clock
from ragnarok.ingest.textutil import html_to_text

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
DEVICE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode"
SCOPES = "offline_access Mail.Read Calendars.Read"
DOMAINS = ("graph.microsoft.com", "login.microsoftonline.com")
SELECT = "id,subject,from,receivedDateTime,conversationId,hasAttachments,body,isDraft"
PREFER = 'outlook.body-content-type="text", IdType="ImmutableId"'
MAX_TEXT = 20000


class MicrosoftAuth:
    def __init__(self, client_id: str, refresh_token: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._id, self._refresh = client_id, refresh_token
        self.http = client or new_client()
        self._access: str | None = None
        self._expires = 0
        self.rotated: str | None = None  # Microsoft may rotate the refresh token; the caller stores the new one in the vault

    async def token(self) -> str:
        now = get_clock().now_ms()
        if self._access and now < self._expires - 60_000:
            return self._access
        r = await request(self.http, "POST", TOKEN_URL, data={"client_id": self._id, "refresh_token": self._refresh, "grant_type": "refresh_token", "scope": SCOPES})
        d = r.json()
        if r.status_code != 200 or "access_token" not in d:
            raise ConnectorError(f"microsoft token: {d.get('error', r.status_code)}", code="E_AUTH")
        self._access, self._expires = d["access_token"], now + int(d.get("expires_in", 3600)) * 1000
        if d.get("refresh_token") and d["refresh_token"] != self._refresh:
            self._refresh = self.rotated = d["refresh_token"]
        return self._access

    async def get(self, url: str, *, prefer: str | None = None, **params: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {await self.token()}", **({"Prefer": prefer} if prefer else {})}
        r = await request(self.http, "GET", url, headers=headers, params=params or None)
        if r.status_code >= 400:
            raise ConnectorError(f"graph {r.status_code}", code="E_GRAPH")
        return r.json()  # type: ignore[no-any-return]


async def device_code_start(client_id: str, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    r = await request(client or new_client(), "POST", DEVICE_URL, data={"client_id": client_id, "scope": SCOPES})
    return r.json()  # type: ignore[no-any-return]


async def device_code_poll(client_id: str, device_code: str, client: httpx.AsyncClient | None = None) -> str | None:
    """Returns the refresh token once the person has signed in, or None while still pending."""
    r = await request(client or new_client(), "POST", TOKEN_URL, data={"client_id": client_id, "device_code": device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    d = r.json()
    if d.get("error") == "authorization_pending":
        return None
    if "refresh_token" not in d:
        raise ConnectorError(f"device code: {d.get('error', 'failed')}", code="E_AUTH")
    return str(d["refresh_token"])


class OutlookMail:
    name = "outlook"

    def __init__(self, auth: MicrosoftAuth, *, backfill_days: int = 14) -> None:
        self._a, self._days = auth, backfill_days

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo("outlook", "Outlook mail", ("message",), read_scopes=("Mail.Read",), write_scopes=("Mail.Send",), domains=DOMAINS, default_minimisation="inbox only, 14 days, no attachments read")

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        since = (cursor.value if cursor else {}).get("since") or (datetime.fromtimestamp(get_clock().now_ms() / 1000, UTC) - timedelta(days=self._days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        newest = since
        refs: list[ItemRef] = []
        url: str | None = f"{GRAPH}/me/mailFolders/inbox/messages"
        params: dict[str, Any] | None = {"$filter": f"receivedDateTime ge {since}", "$orderby": "receivedDateTime asc", "$select": SELECT, "$top": 100}
        for _ in range(20):
            if url is None:
                break
            d = await self._a.get(url, prefer=PREFER, **(params or {}))
            for m in d.get("value", []):
                if m.get("isDraft") or not m.get("id"):
                    continue
                newest = max(newest, m.get("receivedDateTime", newest))
                refs.append(ItemRef(m["id"], {"m": m}, priority=1 if cursor else 5))
            url, params = d.get("@odata.nextLink"), None
        return SyncResult(refs, Cursor({"since": newest}))

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        m = ref.hint.get("m")
        if not m:
            return None
        body = m.get("body") or {}
        text, hidden = body.get("content") or "", False
        if (body.get("contentType") or "").lower() == "html":
            text, hidden = html_to_text(text)
        text = text[:MAX_TEXT].strip()
        if not text:
            return None
        e = (m.get("from") or {}).get("emailAddress") or {}
        received = m.get("receivedDateTime")
        return RawItem(
            source=self.name, native_id=ref.native_id, text=text, trust="received", title=" ".join((m.get("subject") or "").split()) or None, thread_id=m.get("conversationId"),
            occurred_at=int(datetime.fromisoformat(received.replace("Z", "+00:00")).timestamp() * 1000) if received else None, html_hidden=hidden, strip_quotes=True,
            author=e.get("name") or e.get("address"), flags={"from": (e.get("address") or "").lower(), "attachments": int(bool(m.get("hasAttachments")))},
        )

    def actions(self) -> list[ActionSpec]:
        return [ActionSpec("mail.send", "Send an email through Outlook", {"to": "string", "subject": "string", "body": "string"}, tier=3)]

    async def health(self) -> Health:
        try:
            d = await self._a.get(f"{GRAPH}/me", **{"$select": "userPrincipalName"})
        except ConnectorError as exc:
            return Health(False, exc.code)
        return Health(True, str(d.get("userPrincipalName", ""))[:3] + "…")


class OutlookCalendar:
    name = "outlook_cal"

    def __init__(self, auth: MicrosoftAuth, *, back_days: int = 14, ahead_days: int = 60) -> None:
        self._a, self._back, self._ahead = auth, back_days, ahead_days

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo("outlook_cal", "Outlook calendar", ("event",), read_scopes=("Calendars.Read",), domains=DOMAINS)

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        now = datetime.fromtimestamp(get_clock().now_ms() / 1000, UTC)
        d = await self._a.get(f"{GRAPH}/me/calendarView", prefer='outlook.timezone="UTC"', startDateTime=(now - timedelta(days=self._back)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              endDateTime=(now + timedelta(days=self._ahead)).strftime("%Y-%m-%dT%H:%M:%SZ"), **{"$top": 250})
        refs = [ItemRef(e["id"], {"e": e, "version": e.get("lastModifiedDateTime", "")}, priority=1 if cursor else 5) for e in d.get("value", []) if not e.get("isCancelled")]
        return SyncResult(refs, Cursor({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}))

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        e = ref.hint.get("e")
        if not e:
            return None
        start, end = (e.get("start") or {}).get("dateTime", ""), (e.get("end") or {}).get("dateTime", "")
        who = ", ".join((a.get("emailAddress") or {}).get("name", "") for a in e.get("attendees", [])[:20])
        lines = [f"Event: {e.get('subject', '(no title)')}", f"When: {start} to {end} UTC"]
        if (e.get("location") or {}).get("displayName"):
            lines.append(f"Where: {e['location']['displayName']}")
        if who:
            lines.append(f"With: {who}")
        if e.get("bodyPreview"):
            lines.append(e["bodyPreview"])
        try:
            occurred = int(datetime.fromisoformat(start.split(".")[0] + "+00:00").timestamp() * 1000)
        except ValueError:
            occurred = None
        return RawItem(source=self.name, native_id=ref.native_id, text="\n".join(lines), trust="received", title=e.get("subject"), kind="event", occurred_at=occurred, deep_link=e.get("webLink"))

    def actions(self) -> list[ActionSpec]:
        return []

    async def health(self) -> Health:
        try:
            await self._a.get(f"{GRAPH}/me/calendar")
        except ConnectorError as exc:
            return Health(False, exc.code)
        return Health(True)
