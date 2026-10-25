"""Notification source: allow-list, secret dropping, cursor, untrusted tag, permission failure message. Built on a database that follows the macOS layout."""

from __future__ import annotations

import plistlib
import sqlite3

import pytest

from ragnarok.connectors.notifications import MacNotificationSource, Notifications, has_secret
from ragnarok.connectors.runner import ConnectorRunner
from ragnarok.core.clock import RealClock, set_clock
from ragnarok.memory.queue import IngestQueue
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.service import MemoryService


def make_db(path, rows):
    c = sqlite3.connect(path)
    c.execute("create table app (app_id integer primary key, identifier text)")
    c.execute("create table record (rec_id integer primary key, app_id integer, data blob, delivered_date real)")
    apps = {}
    for rec_id, app, title, body, when in rows:
        apps.setdefault(app, len(apps) + 1)
        c.execute("insert or ignore into app values (?,?)", (apps[app], app))
        c.execute("insert into record values (?,?,?,?)", (rec_id, apps[app], plistlib.dumps({"req": {"titl": title, "subt": "", "body": body}}, fmt=plistlib.FMT_BINARY), when))
    c.commit()
    c.close()


ROWS = [
    (1, "ru.keepcoder.Telegram", "Ravi", "Harbour permit renews in June", 800_000_000.0),
    (2, "com.apple.mail", "Bank", "Your verification code is 482913", 800_000_100.0),
    (3, "ru.keepcoder.Telegram", "Asha", "Card 4111 1111 1111 1111 was charged", 800_000_200.0),
    (4, "com.example.game", "Game", "Your energy is full", 800_000_300.0),
    (5, "ru.keepcoder.Telegram", "Meera", "Standup moved to ten tomorrow", 800_000_400.0),
]


@pytest.fixture
def world(tmp_path, keys):
    set_clock(RealClock())
    mem = MemoryService(tmp_path / "db" / "d.db", keys)
    db = tmp_path / "notif.db"
    make_db(db, ROWS)
    yield mem, keys, db
    mem.close()


def test_secret_detector():
    assert has_secret("Your verification code is 482913") and has_secret("482913 is your OTP") and has_secret("Card 4111 1111 1111 1111") and has_secret("your password is hunter2")
    assert not has_secret("Standup moved to ten tomorrow") and not has_secret("Meeting in room 4120 at 3")


async def test_off_by_default_reads_nothing(world):
    mem, keys, db = world
    run = ConnectorRunner(mem, IngestQueue(mem), Notifications(MacNotificationSource(db), []), "work")
    assert (await run.cycle()).queued == 0 and MemoryReader(mem.db_path, keys).count("items") == 0


async def test_allow_list_drops_other_apps_and_secrets_and_tags_untrusted(world):
    mem, keys, db = world
    run = ConnectorRunner(mem, IngestQueue(mem), Notifications(MacNotificationSource(db), ["ru.keepcoder.Telegram", "com.apple.mail"]), "work")
    rep = await run.cycle()
    assert rep.ingested == 2, "only the two harmless Telegram messages: the code, the card number and the game are dropped"
    r = MemoryReader(mem.db_path, keys)
    cid = r.compartment_id("work")
    assert r.fts_chunks("harbour permit", cid) and r.fts_chunks("Standup", cid) and not r.fts_chunks("482913", cid) and not r.fts_chunks("4111", cid) and not r.fts_chunks("energy", cid)
    assert {t for (t,) in r._r.execute("SELECT trust FROM items")} == {"received"}
    assert (await run.cycle()).queued == 0, "cursor advanced past everything seen, including what was dropped"


async def test_health_explains_missing_permission(tmp_path, monkeypatch):
    src = MacNotificationSource(tmp_path / "nope")
    ok, why = src.available()
    assert not ok and "not found" in why
    import builtins

    real_open = builtins.open

    def deny(p, *a, **k):
        if str(p).endswith("denied.db"):
            raise PermissionError("nope")
        return real_open(p, *a, **k)

    monkeypatch.setattr(builtins, "open", deny)
    ok, why = MacNotificationSource(tmp_path / "denied.db").available()
    assert not ok and "Full Disk Access" in why
