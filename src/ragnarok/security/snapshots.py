"""Reversibility (ARCHITECTURE 5.2 item 8): a snapshot before any bulk change, and trash instead of delete."""

from __future__ import annotations

import shutil
from pathlib import Path

from ragnarok.core.ids import new_id
from ragnarok.memory.ledger import Ledger

BULK_THRESHOLD = 5


class Snapshots:
    def __init__(self, ledger: Ledger, data_dir: Path) -> None:
        self._l, self.dir = ledger, data_dir / "snapshots"

    def take(self, paths: list[Path], scope: str, node_id: str | None = None) -> str:
        snap = self.dir / new_id()
        snap.mkdir(parents=True, exist_ok=True)
        for p in paths:
            if p.is_file():
                dest = snap / p.name
                n = 1
                while dest.exists():
                    dest = snap / f"{n}-{p.name}"
                    n += 1
                shutil.copy2(p, dest)
        return self._l.add_snapshot(scope, str(snap), node_id)

    @staticmethod
    def needed(paths: list[Path], outside_workspace: bool) -> bool:
        return len(paths) > BULK_THRESHOLD or outside_workspace


def to_trash(path: Path) -> None:
    """Never a hard delete: the OS trash keeps it recoverable."""
    from send2trash import send2trash

    send2trash(str(path))
