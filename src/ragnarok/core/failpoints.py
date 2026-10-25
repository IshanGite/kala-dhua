"""Named failpoints for crash testing.

Code marks a point with failpoint("after_intent_logged"). Normally this does nothing. A test arms a
point through RAGNAROK_FAILPOINT (comma separated names) or a control file named by
RAGNAROK_FAILPOINT_FILE; the process then exits abruptly with os._exit, which skips all cleanup the way
a crash does and behaves the same on macOS and Windows.
"""

from __future__ import annotations

import os
from pathlib import Path

CRASH_EXIT_CODE = 137


def _armed() -> set[str]:
    names: set[str] = set()
    env = os.environ.get("RAGNAROK_FAILPOINT", "")
    names.update(n.strip() for n in env.split(",") if n.strip())
    ctrl = os.environ.get("RAGNAROK_FAILPOINT_FILE")
    if ctrl:
        try:
            names.update(
                n.strip() for n in Path(ctrl).read_text(encoding="utf-8").splitlines() if n.strip()
            )
        except OSError:
            pass
    return names


def failpoint(name: str) -> None:
    if name in _armed():
        os._exit(CRASH_EXIT_CODE)
