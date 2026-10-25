"""SQLCipher connections with the memory guardrails built in (ARCHITECTURE 7, baseline "One writer").

* open_writer: the single writable connection, owned by the Memory Service.
* open_reader: read-only (PRAGMA query_only) for retrieval and everything else.
* Both install an authorizer that denies ATTACH/DETACH, unknown PRAGMAs, extension loading and, after
  migrations, any schema change. A forgotten bug therefore cannot copy data to another file.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlcipher3 import dbapi2 as sqlite

from ragnarok.core.errors import StoreError, WrongKeyError
from ragnarok.store.crypto import KeyHierarchy

# SQLite authorizer action codes (stable, documented in the SQLite C API).
_DDL = {1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16, 17, 26, 29, 30}
_ATTACH, _DETACH, _PRAGMA, _FUNCTION = 24, 25, 19, 31
_OK, _DENY = 0, 1

READ_PRAGMAS = {
    "table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list",
    "foreign_key_check", "quick_check", "integrity_check", "user_version", "cipher_version",
    "page_count", "freelist_count", "journal_mode", "data_version", "compile_options", "database_list",
}
WRITER_PRAGMAS = READ_PRAGMAS | {"wal_checkpoint", "incremental_vacuum", "optimize"}
DENIED_FUNCTIONS = {"load_extension", "fts3_tokenizer"}

BUSY_TIMEOUT_MS = 5000


def _make_authorizer(*, writer: bool, allow_ddl: bool):
    pragmas = WRITER_PRAGMAS if writer else READ_PRAGMAS

    def authorize(action: int, arg1: Any, arg2: Any, db_name: Any, source: Any) -> int:
        if action in (_ATTACH, _DETACH):
            return _DENY
        if action == _PRAGMA:
            name = (arg1 or "").lower()
            return _OK if name in pragmas else _DENY
        if action == _FUNCTION and (arg2 or "").lower() in DENIED_FUNCTIONS:
            return _DENY
        if action in _DDL and not allow_ddl:
            return _DENY
        return _OK

    return authorize


def _connect(
    path: Path, keys: KeyHierarchy, *, writer: bool, allow_ddl: bool = False, authorizer: bool = True
) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite.connect(str(path), isolation_level=None, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        conn.execute(f"PRAGMA key = \"{keys.db_key_sql}\"")
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite.DatabaseError as exc:
            raise WrongKeyError("database could not be opened with this key") from exc
        if writer:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
        else:
            conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
        conn.row_factory = sqlite.Row
        if authorizer:
            conn.set_authorizer(_make_authorizer(writer=writer, allow_ddl=allow_ddl))
    except Exception:
        conn.close()
        raise
    return conn


def open_writer(path: Path, keys: KeyHierarchy) -> Any:
    return _connect(path, keys, writer=True)


def open_reader(path: Path, keys: KeyHierarchy) -> Any:
    return _connect(path, keys, writer=False)


def open_for_migration(path: Path, keys: KeyHierarchy) -> Any:
    return _connect(path, keys, writer=True, allow_ddl=True, authorizer=False)


@contextlib.contextmanager
def transaction(conn: Any) -> Iterator[Any]:
    """BEGIN IMMEDIATE ... COMMIT, rolled back on any exception. One committed step at a time (NFR3.2)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def cipher_version(conn: Any) -> str:
    row = conn.execute("PRAGMA cipher_version").fetchone()
    if not row or not row[0]:
        raise StoreError("SQLCipher is not active on this connection")
    return str(row[0])
