"""Taking a picture of the screen, only when the user asks. macOS: `screencapture`. Windows: Tushar (PrintWindow / Windows.Graphics.Capture)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from ragnarok.core.errors import RagnarokError


class ScreenUnavailable(RagnarokError):
    code = "E_SCREEN"


MAC_HELP = ("macOS has not allowed screen capture for the app that started Ragnarok. Open System Settings > Privacy & Security > Screen & System Audio Recording, "
            "turn it on for Terminal (or the app you start Ragnarok from), then quit and restart that app.")


def capture_screen(timeout: float = 10.0) -> bytes:
    """PNG bytes of the main display. Never written anywhere permanent: the temp file is removed immediately."""
    if sys.platform != "darwin":
        raise ScreenUnavailable("screen capture is not implemented on this operating system yet (see docs/HANDOVER_TUSHAR.md)")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "s.png"
        try:
            r = subprocess.run(["screencapture", "-x", "-t", "png", str(out)], capture_output=True, timeout=timeout, check=False)  # noqa: S603, S607
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScreenUnavailable("could not run screencapture") from exc
        if r.returncode != 0 or not out.exists() or out.stat().st_size < 500:
            raise ScreenUnavailable(MAC_HELP)
        return out.read_bytes()
