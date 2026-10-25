from __future__ import annotations

import os

# Tests never touch the real OS secret store or the real data directory.
os.environ.setdefault("RAGNAROK_SECRETS", "memory")
os.environ.setdefault("PYTHONUTF8", "1")

import pytest  # noqa: E402

from ragnarok.platform import secrets  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "home"))
    secrets.set_default_store(secrets.MemoryStore())
    yield
    secrets.set_default_store(None)


@pytest.fixture
def keys():
    from ragnarok.store.crypto import KeyHierarchy

    return KeyHierarchy(bytes(range(32)))


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "db" / "t.db"


@pytest.fixture
def migrated(db_path, keys):
    from ragnarok.store.migrate import migrate

    migrate(db_path, keys)
    return db_path
