"""Kill switch (SEC-09): `ragnarok panic` and canary trips halt all tasks and lock the store.

The flag is a file under run/ so every process (daemon, workers, CLI) sees the same state and a restart does not clear it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from ragnarok.core.clock import get_clock
from ragnarok.core.logging import log

_hooks: list[Callable[[str], None]] = []
_lock = threading.Lock()


def _flag(run_dir: Path) -> Path:
    return run_dir / "PANIC"


def on_panic(hook: Callable[[str], None]) -> None:
    """Register a callback (cancel tasks, kill sandboxes, lock the store)."""
    with _lock:
        _hooks.append(hook)


def trigger(run_dir: Path, reason_code: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    _flag(run_dir).write_text(f"{get_clock().now_ms()} {reason_code}\n", encoding="utf-8", newline="\n")
    log("PANIC", reason=reason_code)
    for hook in list(_hooks):
        try:
            hook(reason_code)
        except Exception:  # noqa: BLE001 - one failing hook must not stop the others
            log("panic_hook_failed")


def is_panicked(run_dir: Path) -> bool:
    return _flag(run_dir).exists()


def clear(run_dir: Path) -> None:
    """Only the user, from the CLI or UI after reviewing the incident."""
    _flag(run_dir).unlink(missing_ok=True)
