"""macOS Seatbelt backend (Spike P0-05, verified on macOS with Apple's system.sb baseline).

The kernel enforces the profile on every file, network and process operation, so the skill cannot loosen it from inside.
Findings from the spike (see docs/spikes/05-seatbelt.md): a bare deny-default profile aborts even /usr/bin/true on current
macOS, so the profile imports Apple's system.sb baseline and then denies network (even loopback), fork and everything not
granted. `sandbox-exec` is deprecated by Apple but works; if the profile cannot be applied the skill does not run.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

from ragnarok.core.errors import SandboxUnavailable
from ragnarok.core.ids import sha256_hex
from ragnarok.sandbox.contract import Granted, Limits, ProbeResult

SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _q(path: Path | str) -> str:
    s = os.path.realpath(str(path))
    if any(c in s for c in '"\n\r\\'):
        raise SandboxUnavailable("path contains a character not allowed in a sandbox profile")  # fail closed
    return f'"{s}"'


def skill_interpreter() -> Path:
    """The BASE interpreter (not the venv shim): skills run with `-I`, stdlib only, under the sandbox."""
    return Path(getattr(sys, "_base_executable", sys.executable)).resolve()


class SeatbeltBackend:
    name = "seatbelt"

    def __init__(self) -> None:
        self._dirs: list[Path] = []
        self._lock = threading.Lock()

    def probe(self) -> ProbeResult:
        if sys.platform != "darwin":
            return ProbeResult(self.name, False, "not macOS")
        if not Path(SANDBOX_EXEC).exists():
            return ProbeResult(self.name, False, "sandbox-exec not found")
        return ProbeResult(self.name, True, "sandbox-exec present")

    def profile(self, argv: list[str], granted: Granted, limits: Limits) -> tuple[str, str]:
        exe = Path(os.path.realpath(argv[0]))
        base = Path(os.path.realpath(sys.base_prefix))
        lines = [
            "(version 1)",
            "(deny default)",
            '(import "system.sb")',
            f"(allow process-exec (literal {_q(exe)}))",
            f"(allow file-read* (subpath {_q(base)}))",
        ]
        if granted.skill_dir:
            lines.append(f"(allow file-read* (subpath {_q(granted.skill_dir)}))")
        lines.append(f"(allow file-read* file-write* (subpath {_q(granted.run_dir)}))")
        for p in granted.read_paths:
            lines.append(f"(allow file-read* (subpath {_q(p)}))")
        for p in granted.write_paths:
            lines.append(f"(allow file-read* file-write* (subpath {_q(p)}))")
        for p in granted.deny_paths:  # the data directory and credential folders stay denied whatever else is granted
            lines.append(f"(deny file-read* file-write* (subpath {_q(p)}))")
        if not granted.network:
            lines.append("(deny network*)")
        if not limits.allow_fork:
            lines.append("(deny process-fork)")
        text = "\n".join(lines) + "\n"
        normalized = text.replace(_q(base), '"<PYBASE>"').replace(_q(granted.run_dir), '"<RUN>"')
        if granted.skill_dir:
            normalized = normalized.replace(_q(granted.skill_dir), '"<SKILL>"')
        return text, sha256_hex(normalized)

    def wrap(self, argv: list[str], granted: Granted, limits: Limits) -> tuple[list[str], str]:
        probe = self.probe()
        if not probe.available:
            raise SandboxUnavailable(probe.detail)
        text, phash = self.profile(argv, granted, limits)
        profile_dir = Path(tempfile.mkdtemp(prefix="rag-sb-"))
        profile_path = profile_dir / "profile.sb"
        profile_path.write_text(text, encoding="utf-8", newline="\n")
        with self._lock:
            self._dirs.append(profile_dir)
        return [SANDBOX_EXEC, "-f", str(profile_path), *argv], phash

    def cleanup(self) -> None:
        with self._lock:
            dirs, self._dirs = self._dirs, []
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
