"""Operating-system notifications, so proactive alerts reach the user even when the window is hidden. Windows: Tushar (toast through the WinRT API or a tray balloon)."""

from __future__ import annotations

import subprocess
import sys


def _clean(text: str, limit: int) -> str:
    return "".join(c for c in text if c.isprintable()).replace('"', "'").replace("\\", " ")[:limit]


def notify(title: str, body: str) -> bool:
    """Best effort; returns whether the OS accepted it. Arguments are scrubbed: nothing here is ever interpreted as a command."""
    if sys.platform != "darwin":
        return False
    script = f'display notification "{_clean(body, 180)}" with title "{_clean(title, 60)}"'
    try:
        return subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5, check=False).returncode == 0  # noqa: S603, S607
    except (OSError, subprocess.TimeoutExpired):
        return False
