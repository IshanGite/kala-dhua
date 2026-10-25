"""Google: Gmail, Calendar, Drive and Meet transcripts over REST with one OAuth refresh token.

Prior work: message parsing and the authentication-results check adapted from groupify. Official-docs notes (verify live):
token endpoint https://oauth2.googleapis.com/token (grant_type=refresh_token); read-only scopes gmail.readonly, calendar.readonly,
drive.readonly, meetings.space.readonly; Gmail users.messages.list(q, pageToken) then users.messages.get(format=full); Calendar events.list
(updatedMin, singleEvents, showDeleted); Drive files.list(q, fields) then files.export (Docs/Sheets/Slides) or files.get alt=media;
Meet v2 conferenceRecords -> transcripts -> entries (transcripts exist only if transcription was on, on a Workspace plan; entries live 30 days).
The refresh token is the read token; sending mail or writing events uses a different token with a different scope.
"""

from __future__ import annotations

import base64
import tempfile
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from typing import Any

import httpx

from ragnarok.connectors.base import ActionSpec, ConnectorInfo, Cursor, Health, ItemRef, RawItem, SyncResult
from ragnarok.connectors.http import ConnectorError, new_client, request
from ragnarok.core.clock import get_clock
from ragnarok.ingest.textutil import html_to_text

TOKEN_URL = "https://oauth2.googleapis.com/token"
DOMAINS = ("googleapis.com", "oauth2.googleapis.com", "accounts.google.com")
READ_SCOPES = {
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
    "calendar": "https://www.googleapis.com/auth/calendar.readonly",
    "drive": "https://www.googleapis.com/auth/drive.readonly",
    "meet": "https://www.googleapis.com/auth/meetings.space.readonly",
}


WRITE_SCOPES = {
    "gmail": ("https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.compose"),
    "calendar": ("https://www.googleapis.com/auth/calendar.events",),
    "drive": ("https://www.googleapis.com/auth/drive.file",),
    "meet": ("https://www.googleapis.com/auth/meetings.space.created",),
}


class GoogleAuth:
    """Turns a refresh token into short-lived access tokens, kept in memory only."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._id, self._secret, self._refresh = client_id, client_secret, refresh_token
        self.http = client or new_client()
        self._access: str | None = None
        self._expires = 0

    async def token(self) -> str:
        now = get_clock().now_ms()
        if self._access and now < self._expires - 60_000:
            return self._access
        r = await request(self.http, "POST", TOKEN_URL, data={"client_id": self._id, "client_secret": self._secret, "refresh_token": self._refresh, "grant_type": "refresh_token"})
        d = r.json()
        if r.status_code != 200 or "access_token" not in d:
            raise ConnectorError(f"google token: {d.get('error', r.status_code)}", code="E_AUTH")
        self._access, self._expires = d["access_token"], now + int(d.get("expires_in", 3600)) * 1000
        return self._access

    async def get(self, url: str, **params: Any) -> httpx.Response:
        r = await request(self.http, "GET", url, headers={"Authorization": f"Bearer {await self.token()}"}, params=params or None)
        if r.status_code >= 400:
            raise ConnectorError(f"google {r.status_code}", code="E_GOOGLE")
        return r


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def authentication_passed(headers: dict[str, str]) -> bool:
    """Gmail records SPF/DKIM/DMARC in Authentication-Results. Fail closed: no header means not trusted."""
    res = " ".join(v.lower() for k, v in headers.items() if k.lower() == "authentication-results")
    return "dmarc=pass" in res or ("spf=pass" in res and "dkim=pass" in res)


def is_automatic(headers: dict[str, str]) -> bool:
    h = {k.lower(): v.lower() for k, v in headers.items()}
    if h.get("auto-submitted", "no") != "no" or h.get("precedence", "") in ("bulk", "junk", "list", "auto_reply"):
        return True
    return any(k in h for k in ("x-autoreply", "x-autorespond", "list-id", "list-unsubscribe"))


def parse_gmail(msg: dict[str, Any]) -> dict[str, Any]:
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    plain: list[str] = []
    html: list[str] = []
    atts = [0]

    def walk(p: dict[str, Any]) -> None:
        mime, body = p.get("mimeType", ""), p.get("body", {})
        if p.get("filename"):
            atts[0] += 1
        elif mime == "text/plain" and body.get("data"):
            plain.append(_b64(body["data"]))
        elif mime == "text/html" and body.get("data"):
            html.append(_b64(body["data"]))
        for sub in p.get("parts", []) or []:
            walk(sub)

    walk(msg.get("payload", {}))
    hidden = False
    text = "\n".join(plain).strip()
    if not text:
        text, hidden = html_to_text("\n".join(html))
    name, addr = parseaddr(headers.get("From", ""))
    return {"text": text, "hidden": hidden, "subject": " ".join(headers.get("Subject", "").split()), "from": addr.lower(), "from_name": name,
            "auth_ok": authentication_passed(headers), "automatic": is_automatic(headers), "attachments": atts[0], "thread": msg.get("threadId"),
            "date": int(msg["internalDate"]) if msg.get("internalDate") else None}


class Gmail:
    name = "gmail"
    API = "https://gmail.googleapis.com/gmail/v1/users/me"

    def __init__(self, auth: GoogleAuth, *, backfill_days: int = 14, max_per_sync: int = 200) -> None:
        self._a, self._days, self._max = auth, backfill_days, max_per_sync

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo("gmail", "Gmail", ("message",), read_scopes=(READ_SCOPES["gmail"],), write_scopes=("https://www.googleapis.com/auth/gmail.send",),
                             domains=DOMAINS, default_minimisation="inbox only, 14 days, no attachments read, automatic mail dropped")

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        after_s = int((cursor.value if cursor else {}).get("after_s", (get_clock().now_ms() - self._days * 86_400_000) // 1000))
        refs: list[ItemRef] = []
        token = ""
        newest = after_s
        for _ in range(10):
            d = (await self._a.get(f"{self.API}/messages", q=f"in:inbox after:{after_s}", maxResults=100, **({"pageToken": token} if token else {}))).json()
            for m in d.get("messages", []):
                refs.append(ItemRef(m["id"], {}, priority=1 if cursor else 5))
            token = d.get("nextPageToken", "")
            if not token or len(refs) >= self._max:
                break
        # internalDate is only known after fetch; the cursor moves to "now" minus a safety overlap and the queue de-duplicates by message id.
        newest = max(newest, (get_clock().now_ms() // 1000) - 120)
        return SyncResult(refs, Cursor({"after_s": newest}))

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        msg = (await self._a.get(f"{self.API}/messages/{ref.native_id}", format="full")).json()
        p = parse_gmail(msg)
        if p["automatic"] or not p["text"]:
            return None
        flags = {"auth_ok": p["auth_ok"], "from": p["from"], "attachments": p["attachments"]}
        return RawItem(source=self.name, native_id=ref.native_id, text=p["text"], trust="received", title=p["subject"] or None, kind="message", visibility="private",
                       thread_id=p["thread"], occurred_at=p["date"], html_hidden=p["hidden"], strip_quotes=True, author=p["from_name"] or p["from"], flags=flags)

    def actions(self) -> list[ActionSpec]:
        return [ActionSpec("mail.send", "Send an email", {"to": "string", "subject": "string", "body": "string"}, tier=3)]

    async def health(self) -> Health:
        try:
            d = (await self._a.get(f"{self.API}/profile")).json()
        except ConnectorError as exc:
            return Health(False, exc.code)
        return Health(True, f"{d.get('messagesTotal', '?')} messages")


class Calendar:
    name = "gcal"
    API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

    def __init__(self, auth: GoogleAuth, *, backfill_days: int = 30, ahead_days: int = 60) -> None:
        self._a, self._back, self._ahead = auth, backfill_days, ahead_days

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo("gcal", "Google Calendar", ("event",), read_scopes=(READ_SCOPES["calendar"],), write_scopes=("https://www.googleapis.com/auth/calendar.events",), domains=DOMAINS)

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        now = datetime.fromtimestamp(get_clock().now_ms() / 1000, UTC)
        updated_min = (cursor.value if cursor else {}).get("updated_min")
        t_min = (now - timedelta(days=self._back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        t_max = (now + timedelta(days=self._ahead)).strftime("%Y-%m-%dT%H:%M:%SZ")
        refs: list[ItemRef] = []
        # Google rejects updatedMin older than about three weeks (410): the first run, or a long gap, reads a time window instead.
        use_updated = bool(updated_min)
        token = ""
        for _ in range(10):
            window = {"updatedMin": updated_min} if use_updated else {"timeMin": t_min}
            try:
                d = (await self._a.get(self.API, singleEvents="true", timeMax=t_max, maxResults=250, **window, **({"pageToken": token} if token else {}))).json()
            except ConnectorError as exc:
                if use_updated and "410" in str(exc):
                    use_updated, token = False, ""
                    continue
                raise
            for e in d.get("items", []):
                if e.get("status") == "cancelled":
                    continue
                refs.append(ItemRef(e["id"], {"event": e, "version": e.get("updated", "")}, priority=1 if cursor else 5))
            token = d.get("nextPageToken", "")
            if not token:
                break
        return SyncResult(refs, Cursor({"updated_min": now.strftime("%Y-%m-%dT%H:%M:%SZ")}))

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        e = ref.hint.get("event")
        if not e:
            return None
        start = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date", "")
        end = (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date", "")
        who = ", ".join(a.get("displayName") or a.get("email", "") for a in e.get("attendees", [])[:20])
        lines = [f"Event: {e.get('summary', '(no title)')}", f"When: {start} to {end}"]
        if e.get("location"):
            lines.append(f"Where: {e['location']}")
        if who:
            lines.append(f"With: {who}")
        if e.get("description"):
            lines.append(e["description"])
        own = (e.get("organizer") or {}).get("self", False)
        occurred = None
        try:
            occurred = int(datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            pass
        return RawItem(source=self.name, native_id=ref.native_id, text="\n".join(lines), trust="user_file" if own else "received", title=e.get("summary"), kind="event",
                       occurred_at=occurred, deep_link=e.get("htmlLink"))

    def actions(self) -> list[ActionSpec]:
        return [ActionSpec("calendar.write", "Create or change a calendar event", {"summary": "string", "start": "string", "end": "string"}, tier=2)]

    async def health(self) -> Health:
        try:
            await self._a.get(self.API, maxResults=1)
        except ConnectorError as exc:
            return Health(False, exc.code)
        return Health(True)


EXPORTS = {"application/vnd.google-apps.document": "text/plain", "application/vnd.google-apps.presentation": "text/plain", "application/vnd.google-apps.spreadsheet": "text/csv"}
TEXT_MIMES = {"text/plain", "text/markdown", "text/csv", "application/json"}
PARSEABLE = {"application/pdf": ".pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"}


class Drive:
    name = "gdrive"
    API = "https://www.googleapis.com/drive/v3/files"

    def __init__(self, auth: GoogleAuth, *, backfill_days: int = 30, max_bytes: int = 20 * 1024 * 1024) -> None:
        self._a, self._days, self._max = auth, backfill_days, max_bytes

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo("gdrive", "Google Drive", ("file",), read_scopes=(READ_SCOPES["drive"],), domains=DOMAINS, default_minimisation="documents, sheets, slides, PDFs, docx, text; recent only")

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        since = (cursor.value if cursor else {}).get("modified") or (datetime.fromtimestamp(get_clock().now_ms() / 1000 - self._days * 86400, UTC)).strftime("%Y-%m-%dT%H:%M:%S")
        now = datetime.fromtimestamp(get_clock().now_ms() / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S")
        refs: list[ItemRef] = []
        token = ""
        for _ in range(10):
            d = (await self._a.get(self.API, q=f"modifiedTime > '{since}' and trashed = false and mimeType != 'application/vnd.google-apps.folder'",
                                   fields="nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink)", pageSize=100, **({"pageToken": token} if token else {}))).json()
            for f in d.get("files", []):
                if f["mimeType"] in EXPORTS or f["mimeType"] in TEXT_MIMES or f["mimeType"] in PARSEABLE:
                    refs.append(ItemRef(f["id"], {"file": f, "version": f.get("modifiedTime", "")}, priority=1 if cursor else 5))
            token = d.get("nextPageToken", "")
            if not token:
                break
        return SyncResult(refs, Cursor({"modified": now}))

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        f = ref.hint.get("file")
        if not f:
            return None
        mime = f["mimeType"]
        if int(f.get("size", 0) or 0) > self._max:
            return None
        if mime in EXPORTS:
            text = (await self._a.get(f"{self.API}/{f['id']}/export", mimeType=EXPORTS[mime])).text
        elif mime in TEXT_MIMES:
            text = (await self._a.get(f"{self.API}/{f['id']}", alt="media")).text
        else:
            from ragnarok.ingest.parse import parse_file

            data = (await self._a.get(f"{self.API}/{f['id']}", alt="media")).content
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / f"f{PARSEABLE[mime]}"
                p.write_bytes(data)
                text = parse_file(p)["text"]
        occurred = int(datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00")).timestamp() * 1000) if f.get("modifiedTime") else None
        return RawItem(source=self.name, native_id=ref.native_id, text=text, trust="user_file", title=f.get("name"), kind="file", occurred_at=occurred, deep_link=f.get("webViewLink"))

    def actions(self) -> list[ActionSpec]:
        return []

    async def health(self) -> Health:
        try:
            await self._a.get(self.API, pageSize=1)
        except ConnectorError as exc:
            return Health(False, exc.code)
        return Health(True)


class Meet:
    name = "gmeet"
    API = "https://meet.googleapis.com/v2"

    def __init__(self, auth: GoogleAuth, *, backfill_days: int = 14) -> None:
        self._a, self._days = auth, backfill_days

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo("gmeet", "Google Meet transcripts", ("meeting",), read_scopes=(READ_SCOPES["meet"],), domains=("meet.googleapis.com", "oauth2.googleapis.com"))

    async def _pages(self, path: str, key: str, **params: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = ""
        for _ in range(20):
            d = (await self._a.get(f"{self.API}/{path}", pageSize=100, **params, **({"pageToken": token} if token else {}))).json()
            out += d.get(key, [])
            token = d.get("nextPageToken", "")
            if not token:
                break
        return out

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        since = (cursor.value if cursor else {}).get("since") or datetime.fromtimestamp(get_clock().now_ms() / 1000 - self._days * 86400, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        now = datetime.fromtimestamp(get_clock().now_ms() / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        refs: list[ItemRef] = []
        for c in await self._pages("conferenceRecords", "conferenceRecords", filter=f'start_time>="{since}"'):
            if not c.get("endTime"):
                continue
            for t in await self._pages(f"{c['name']}/transcripts", "transcripts"):
                if t.get("state") == "FILE_GENERATED":
                    refs.append(ItemRef(t["name"], {"start": c.get("startTime")}, priority=1 if cursor else 5))
        return SyncResult(refs, Cursor({"since": now}))

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        lines: list[str] = []
        names: dict[str, str] = {}
        for e in await self._pages(f"{ref.native_id}/entries", "transcriptEntries"):
            who = e.get("participant", "")
            if who not in names:
                try:
                    names[who] = (await self._a.get(f"{self.API}/{who}")).json().get("signedinUser", {}).get("displayName", "Participant")
                except ConnectorError:
                    names[who] = "Participant"
            lines.append(f"{names[who]}: {e.get('text', '').strip()}")
        if not lines:
            return None
        start = ref.hint.get("start")
        occurred = int(datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp() * 1000) if start else None
        return RawItem(source=self.name, native_id=ref.native_id, text="\n".join(lines), trust="received", title="Meeting transcript", kind="meeting", occurred_at=occurred)

    def actions(self) -> list[ActionSpec]:
        return []

    async def health(self) -> Health:
        try:
            await self._pages("conferenceRecords", "conferenceRecords")
        except ConnectorError as exc:
            return Health(False, exc.code)
        return Health(True)
