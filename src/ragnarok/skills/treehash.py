"""Hash of a skill folder: sorted relative posix paths plus content hashes. Identical on macOS and Windows (D-29)."""

from __future__ import annotations

import hashlib
from pathlib import Path

IGNORED_DIRS = {"__pycache__", ".git"}
IGNORED_FILES = {".DS_Store", "Thumbs.db"}


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*"), key=lambda x: x.relative_to(root).as_posix()):
        rel = p.relative_to(root)
        if any(part in IGNORED_DIRS for part in rel.parts) or p.name in IGNORED_FILES or p.suffix == ".pyc" or not p.is_file():
            continue
        h.update(rel.as_posix().encode("utf-8") + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()
