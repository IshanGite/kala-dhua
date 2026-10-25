"""NFR1.2: the memory database is encrypted at rest with a user-held key, and guarded by an authorizer."""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from ragnarok.core.errors import WrongKeyError
from ragnarok.store.crypto import KeyHierarchy
from ragnarok.store.db import open_reader, open_writer, transaction
from ragnarok.store.migrate import migrate

SECRET = "ZEBRA-UNIQUE-PLAINTEXT-MARKER-91827"


def _seed(path, keys):
    w = open_writer(path, keys)
    with transaction(w):
        w.execute("INSERT INTO compartments(id,name,created_at) VALUES ('c1','work',1)")
        w.execute(
            "INSERT INTO items(id,compartment_id,source,native_id,content_hash,trust,status,ingested_at)"
            " VALUES ('i1','c1','test','n1','h','user_file','raw',1)"
        )
        w.execute("INSERT INTO chunks(id,item_id,ordinal,text) VALUES ('k1','i1',0,?)", (f"the {SECRET} renewal clause",))
    return w


def test_NFR1_2_strings_scan_finds_no_plaintext(migrated, keys):
    w = _seed(migrated, keys)
    w.execute("PRAGMA wal_checkpoint(FULL)")
    w.close()
    for f in migrated.parent.glob("t.db*"):
        assert SECRET.encode() not in f.read_bytes(), f"plaintext found in {f.name}"


def test_NFR1_2_header_is_not_plain_sqlite(migrated, keys):
    _seed(migrated, keys).close()
    assert not migrated.read_bytes()[:16].startswith(b"SQLite format 3")


def test_NFR1_2_wrong_key_refused(migrated, keys):
    _seed(migrated, keys).close()
    with pytest.raises(WrongKeyError):
        open_reader(migrated, KeyHierarchy(bytes(reversed(range(32)))))


def test_NFR1_2_fts_works_inside_encrypted_file(migrated, keys):
    w = _seed(migrated, keys)
    n = w.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'renewal'").fetchone()[0]
    assert n == 1


def test_authorizer_denies_attach(migrated, keys):
    w = open_writer(migrated, keys)
    with pytest.raises(Exception, match="not authorized|authoriz"):
        w.execute("ATTACH DATABASE ':memory:' AS stolen")


def test_authorizer_denies_schema_changes_after_migration(migrated, keys):
    w = open_writer(migrated, keys)
    with pytest.raises(Exception, match="not authorized|authoriz"):
        w.execute("CREATE TABLE sneaky(x)")
    with pytest.raises(Exception, match="not authorized|authoriz"):
        w.execute("DROP TABLE items")


def test_authorizer_denies_unknown_pragmas(migrated, keys):
    w = open_writer(migrated, keys)
    with pytest.raises(Exception, match="not authorized|authoriz"):
        w.execute("PRAGMA writable_schema = ON")


def test_reader_connection_cannot_write(migrated, keys):
    r = open_reader(migrated, keys)
    with pytest.raises(Exception, match="readonly|read-only|query_only"):
        r.execute("INSERT INTO compartments(id,name,created_at) VALUES ('x','y',1)")


def test_events_are_append_only(migrated, keys):
    w = open_writer(migrated, keys)
    with transaction(w):
        w.execute("INSERT INTO compartments(id,name,created_at) VALUES ('c1','work',1)")
        w.execute(
            "INSERT INTO events(id,seq,compartment_id,ts,kind,actor,content,trust,prev_hash,hash)"
            " VALUES ('e1',1,'c1',1,'note','user','hello','user_direct','0','h1')"
        )
    with pytest.raises(Exception, match="append-only"):
        w.execute("UPDATE events SET content='tampered' WHERE id='e1'")


def test_migration_is_idempotent_and_versioned(db_path, keys):
    v1 = migrate(db_path, keys)
    v2 = migrate(db_path, keys)
    assert v1 == v2 >= 2


def test_blob_keys_wrap_and_unwrap(keys):
    key = bytes(range(32))
    wrapped = keys.wrap(key, aad=b"blob-1")
    assert wrapped != key and keys.unwrap(wrapped, aad=b"blob-1") == key
    with pytest.raises(InvalidTag):
        keys.unwrap(wrapped, aad=b"other")


def test_derived_keys_are_distinct_per_label(keys):
    assert keys.derive("db") != keys.derive("backup") != keys.derive("wrap")
    assert len(keys.derive("db")) == 32 and keys.db_key_sql.startswith("x'")
