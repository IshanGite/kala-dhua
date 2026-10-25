"""Taking a picture of the screen, only when the user asks. macOS: `screencapture`. Windows: Tushar (Windows.Graphics.Capture).
Modes: the whole display (any display), a part the user drags out, or one window the user clicks. The picture is returned as bytes and never written anywhere permanent."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from ragnarok.core.errors import RagnarokError


class ScreenUnavailable(RagnarokError):
    code = "E_SCREEN"


class ScreenCancelled(RagnarokError):
    code = "E_SCREEN_CANCEL"


MAC_HELP = ("macOS has not allowed screen capture for the app that started Ragnarok. Open System Settings > Privacy & Security > Screen & System Audio Recording, "
            "turn it on for Terminal (or the app you start Ragnarok from), then quit and restart that app.")


def list_displays() -> list[dict[str, object]]:
    """The connected displays, main first: [{index, name, main, size}]. `index` is what screencapture -D takes."""
    if sys.platform != "darwin":
        return [{"index": 1, "name": "Main display", "main": True, "size": ""}]
    try:
        r = subprocess.run(["system_profiler", "SPDisplaysDataType", "-json"], capture_output=True, timeout=15, check=False)  # noqa: S603, S607
        gpus = json.loads(r.stdout or b"{}").get("SPDisplaysDataType", [])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        gpus = []
    out = [{"name": str(d.get("_name", "Display")), "main": d.get("spdisplays_main") == "spdisplays_yes", "size": str(d.get("_spdisplays_resolution", "")).split(" @")[0]} for g in gpus for d in g.get("spdisplays_ndrvs", [])]
    out.sort(key=lambda d: not d["main"])  # screencapture numbers the main display 1
    return [{"index": i + 1, **d} for i, d in enumerate(out)] or [{"index": 1, "name": "Main display", "main": True, "size": ""}]


def capture(mode: str = "screen", display: int = 1, timeout: float = 90.0) -> bytes:
    """PNG bytes. mode: 'screen' (a whole display), 'region' (the user drags a rectangle), 'window' (the user clicks a window). Interactive modes wait for the user; Esc cancels."""
    if sys.platform != "darwin":
        raise ScreenUnavailable("screen capture is not implemented on this operating system yet (see docs/HANDOVER_TUSHAR.md)")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "s.png"
        args = ["screencapture", "-x", "-t", "png"]
        if mode == "region":
            args += ["-i", "-s"]
        elif mode == "window":
            args += ["-i", "-w", "-o"]
        else:
            args += ["-D", str(max(1, min(int(display), 8)))]
        try:
            r = subprocess.run([*args, str(out)], capture_output=True, timeout=timeout if mode != "screen" else 15, check=False)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScreenUnavailable("could not run screencapture") from exc
        if mode != "screen" and r.returncode == 0 and not out.exists():
            raise ScreenCancelled("Selection cancelled, nothing was captured.")
        if r.returncode != 0 or not out.exists() or out.stat().st_size < 500:
            raise ScreenUnavailable(MAC_HELP)
        return out.read_bytes()


capture_screen = capture  # the old name


def permission_ok() -> bool | None:
    """True when a capture shows real pixels, False when macOS hands back black (no permission), None when it cannot be tested here."""
    if sys.platform != "darwin":
        return None
    try:
        png = capture("screen", 1)
        from ragnarok.harness.vision import looks_blank

        return not looks_blank(png)
    except RagnarokError:
        return False
