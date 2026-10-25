"""The single data directory (ARCHITECTURE 3.3). Everything persistent lives under it.

RAGNAROK_HOME overrides the location (tests, demos). Otherwise platformdirs picks Application Support
on macOS and %LOCALAPPDATA% on Windows.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

SUBDIRS = ("db", "blobs", "skills", "routes", "logs", "run", "tmp", "canary", "snapshots")


def data_dir() -> Path:
    override = os.environ.get("RAGNAROK_HOME")
    if override:
        return Path(override)
    return Path(platformdirs.user_data_dir("Ragnarok", appauthor=False))


def ensure_dirs(root: Path | None = None) -> Path:
    root = root or data_dir()
    for name in SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def db_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "db" / "ragnarok.db"
