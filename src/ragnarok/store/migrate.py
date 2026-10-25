"""Numbered SQL migrations tracked by PRAGMA user_version. A failing migration rolls back and refuses to start."""

from __future__ import annotations

import re
from pathlib import Path

from ragnarok.core.errors import StoreError
from ragnarok.store.crypto import KeyHierarchy
from ragnarok.store.db import open_for_migration

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def available() -> list[tuple[int, Path]]:
    found = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _NAME.match(p.name)
        if m:
            found.append((int(m.group(1)), p))
    return found


def migrate(path: Path, keys: KeyHierarchy) -> int:
    """Apply all pending migrations. Returns the resulting schema version."""
    conn = open_for_migration(path, keys)
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for number, file in available():
            if number <= current:
                continue
            sql = file.read_text(encoding="utf-8")
            try:
                conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001 - nothing to roll back
                    pass
                raise StoreError(f"migration {file.name} failed: {exc}") from exc
            current = number
        return int(current)
    finally:
        conn.close()
