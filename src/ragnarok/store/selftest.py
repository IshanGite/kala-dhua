"""Encrypted-store self test used by `ragnarok doctor`. Lives in store/ because it writes SQL."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlite

from ragnarok.core.errors import WrongKeyError
from ragnarok.store.crypto import KeyHierarchy
from ragnarok.store.db import cipher_version, open_reader, open_writer
from ragnarok.store.migrate import migrate


def run() -> tuple[bool, str]:
    """Returns (ok, detail). Uses a throwaway key and directory, never the real store."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "selftest.db"
        keys = KeyHierarchy(os.urandom(32))
        version = migrate(path, keys)
        w = open_writer(path, keys)
        cv = cipher_version(w)
        w.execute("INSERT INTO compartments(id,name,created_at) VALUES ('c','work',1)")
        w.execute(
            "INSERT INTO items(id,compartment_id,source,native_id,content_hash,trust,status,ingested_at)"
            " VALUES ('i','c','t','1','h','user_file','raw',1)"
        )
        w.execute("INSERT INTO chunks(id,item_id,ordinal,text) VALUES ('k','i',0,'vendor contract renewal')")
        hit = w.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'contract'").fetchone()[0]
        attach_denied = False
        try:
            w.execute("ATTACH DATABASE ':memory:' AS x")
        except sqlite.DatabaseError:
            attach_denied = True
        w.close()
        wrong_refused = False
        try:
            open_reader(path, KeyHierarchy(os.urandom(32)))
        except WrongKeyError:
            wrong_refused = True
        plaintext_header = path.read_bytes()[:16].startswith(b"SQLite format 3")
    problems = [
        name
        for name, bad in (
            ("fts-failed", hit != 1),
            ("attach-not-denied", not attach_denied),
            ("wrong-key-accepted", not wrong_refused),
            ("plaintext-header", plaintext_header),
        )
        if bad
    ]
    if problems:
        return False, ", ".join(problems)
    return True, f"SQLCipher {cv}, schema v{version}, FTS5 works, wrong key refused, ATTACH denied"
