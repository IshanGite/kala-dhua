"""Desktop notifications as a source: text that other apps showed the user (Telegram, WhatsApp, banking alerts...), read locally.

Privacy rules (all enforced here, tested): OFF unless the user names apps (allow-list of bundle ids); one-time codes, passwords and card
numbers are dropped before anything is stored; the text is tagged `received` (untrusted data, never instructions); nothing is logged.

macOS: the Notification Center keeps records in <Group Containers>/group.com.apple.usernoted/db2/db (SQLite, WAL). The folder is protected:
the process needs Full Disk Access, otherwise open fails with "Operation not permitted" (verified on this Mac, macOS 27). Record layout
(table `record`: rec_id, app_id -> `app`.identifier, data = binary plist with req.{titl,subt,body}, delivered_date in Apple epoch seconds)
follows public forensic write-ups and is verified live only after the permission is granted; see docs/spikes/notifications.md.
Windows (Tushar): UserNotificationListener or %LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db; implement `NotificationSource`.
"""

from __future__ import annotations

import plistlib
import re
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ragnarok.connectors.base import ActionSpec, ConnectorInfo, Cursor, Health, ItemRef, RawItem, SyncResult

APPLE_EPOCH_OFFSET = 978_307_200  # seconds between 1970-01-01 and 2001-01-01
MAC_DB = Path.home() / "Library" / "Group Containers" / "group.com.apple.usernoted" / "db2" / "db"
SECRET = re.compile(
    r"(\b\d{4,8}\b.{0,40}\b(code|otp|passcode|verification|one[- ]?time|pin)\b|\b(code|otp|passcode|verification|one[- ]?time|pin)\b.{0,40}\b\d{4,8}\b|"
    r"\bpassword\b|\b(?:\d[ -]?){13,19}\b|\bcvv\b)", re.I)


@dataclass(frozen=True)
class Notification:
    id: int
    app: str
    title: str
    subtitle: str
    body: str
    ts_ms: int


class NotificationSource(Protocol):
    def read_since(self, last_id: int) -> list[Notification]: ...

    def available(self) -> tuple[bool, str]: ...


def has_secret(text: str) -> bool:
    return bool(SECRET.search(text))


class MacNotificationSource:
    def __init__(self, db: Path | None = None) -> None:
        self._db = db or MAC_DB

    def available(self) -> tuple[bool, str]:
        if sys.platform != "darwin" and self._db == MAC_DB:
            return False, "not macOS"
        try:
            with open(self._db, "rb") as f:  # noqa: PTH123 - the open itself is the permission probe
                f.read(16)
        except PermissionError:
            return False, "needs Full Disk Access (System Settings > Privacy & Security > Full Disk Access)"
        except OSError:
            return False, "notification database not found"
        return True, "ok"

    def read_since(self, last_id: int) -> list[Notification]:
        ok, why = self.available()
        if not ok:
            raise PermissionError(why)
        with tempfile.TemporaryDirectory() as tmp:  # read a private copy: never lock or touch the live database
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(self._db) + suffix)
                if src.exists():
                    shutil.copy2(src, Path(tmp) / ("db" + suffix))
            con = sqlite3.connect(f"file:{Path(tmp) / 'db'}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    "SELECT r.rec_id, a.identifier, r.data, r.delivered_date FROM record r JOIN app a ON a.app_id = r.app_id WHERE r.rec_id > ? ORDER BY r.rec_id", (last_id,)
                ).fetchall()
            finally:
                con.close()
        out: list[Notification] = []
        for rec_id, app, blob, delivered in rows:
            try:
                req = (plistlib.loads(blob) or {}).get("req", {})
            except Exception:  # noqa: BLE001 - a record we cannot parse is skipped, never fatal
                continue
            out.append(Notification(int(rec_id), str(app), str(req.get("titl") or ""), str(req.get("subt") or ""), str(req.get("body") or ""),
                                    int((float(delivered or 0) + APPLE_EPOCH_OFFSET) * 1000)))
        return out


class UnavailableSource:
    def __init__(self, why: str) -> None:
        self._why = why

    def available(self) -> tuple[bool, str]:
        return False, self._why

    def read_since(self, last_id: int) -> list[Notification]:
        raise PermissionError(self._why)


def default_source() -> NotificationSource:
    if sys.platform == "darwin":
        return MacNotificationSource()
    return UnavailableSource("the Windows notification reader is not implemented yet (see docs/HANDOVER_TUSHAR.md)")


class Notifications:
    name = "notifications"

    def __init__(self, source: NotificationSource, allowed_apps: Iterable[str] | Callable[[], Iterable[str]]) -> None:
        self._src = source
        self._allowed_src = allowed_apps if callable(allowed_apps) else (lambda: allowed_apps)

    @property
    def _allowed(self) -> set[str]:
        return {a.lower() for a in self._allowed_src()}

    def describe(self) -> ConnectorInfo:
        return ConnectorInfo("notifications", "Desktop notifications", ("notification",), default_minimisation="only apps you name; codes, passwords and card numbers dropped")

    async def sync(self, cursor: Cursor | None) -> SyncResult:
        last = int((cursor.value if cursor else {}).get("last_id", 0))
        refs: list[ItemRef] = []
        newest = last
        if self._allowed:
            for n in self._src.read_since(last):
                newest = max(newest, n.id)
                if n.app.lower() not in self._allowed:
                    continue
                text = " - ".join(x for x in (n.title, n.subtitle, n.body) if x).strip()
                if not text or has_secret(text):
                    continue
                refs.append(ItemRef(f"{n.app}:{n.id}", {"app": n.app, "text": text, "ts": n.ts_ms, "title": n.title}, priority=1))
        else:
            # Off by default: with no apps named we do not even advance past anything, and nothing is read.
            return SyncResult([], Cursor({"last_id": last}))
        return SyncResult(refs, Cursor({"last_id": newest}))

    async def fetch(self, ref: ItemRef) -> RawItem | None:
        h = ref.hint
        if "text" not in h:
            return None
        return RawItem(source=self.name, native_id=ref.native_id, text=h["text"], trust="received", title=f"{h['app']} notification", kind="notification", visibility="private", occurred_at=h.get("ts"), author=h.get("title") or None)

    def actions(self) -> list[ActionSpec]:
        return []

    async def health(self) -> Health:
        ok, why = self._src.available()
        return Health(ok, why)
