"""Is the computer plugged in, and how long since the person last touched it? Best effort, read-only, no permissions needed. None means 'cannot tell'."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def on_ac() -> bool | None:
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, timeout=5, check=False).stdout.decode("utf-8", "replace")  # noqa: S603, S607
            if "AC Power" in out:
                return True
            if "Battery Power" in out:
                return False
            return None
        if sys.platform.startswith("win"):
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                            ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

            st = Status()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):  # type: ignore[attr-defined]
                return {0: False, 1: True}.get(st.ACLineStatus)
            return None
        for p in Path("/sys/class/power_supply").glob("A*/online"):
            return p.read_text(encoding="utf-8").strip() == "1"
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return None


def idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, timeout=5, check=False).stdout.decode("utf-8", "replace")  # noqa: S603, S607
            m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
            return int(m.group(1)) / 1e9 if m else None
        if sys.platform.startswith("win"):
            import ctypes

            class LastInput(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            li = LastInput(ctypes.sizeof(LastInput), 0)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):  # type: ignore[attr-defined]
                return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0  # type: ignore[attr-defined]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return None
