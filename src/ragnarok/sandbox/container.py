"""Container backend (Docker or Podman): identical behaviour on macOS, Windows and Linux.

Flags: no network, read-only root, all capabilities dropped, no new privileges, pids/memory/cpu limits, non-root user,
only the run folder (rw) and the skill folder (ro) mounted. Needs Docker Desktop or Podman plus virtualization
(WSL 2 on Windows). Marked `needs_container` in tests; skipped with a printed reason when unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from ragnarok.core.errors import SandboxUnavailable
from ragnarok.core.ids import sha256_hex
from ragnarok.sandbox.contract import Granted, Limits, ProbeResult

IMAGE = "python:3.12-slim"


class ContainerBackend:
    name = "container"

    def __init__(self, image: str = IMAGE) -> None:
        self.image = image
        self._engine: str | None = None

    def _find_engine(self) -> str | None:
        for exe in ("docker", "podman"):
            path = shutil.which(exe)
            if path:
                try:
                    r = subprocess.run([path, "info"], capture_output=True, timeout=15, check=False)  # noqa: S603
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if r.returncode == 0:
                    return path
        return None

    def probe(self) -> ProbeResult:
        self._engine = self._find_engine()
        if not self._engine:
            return ProbeResult(self.name, False, "no running Docker or Podman")
        return ProbeResult(self.name, True, Path(self._engine).name)

    def wrap(self, argv: list[str], granted: Granted, limits: Limits) -> tuple[list[str], str]:
        if self._engine is None and not self.probe().available:
            raise SandboxUnavailable("no running Docker or Podman")
        # Map host paths to container paths; the interpreter is the image's own python.
        mapped = ["python" if i == 0 else a for i, a in enumerate(argv)]
        mounts = ["-v", f"{granted.run_dir}:/work:rw"]
        if granted.skill_dir:
            mounts += ["-v", f"{granted.skill_dir}:/skill:ro"]
            mapped = [a.replace(str(granted.skill_dir), "/skill") for a in mapped]
        mapped = [a.replace(str(granted.run_dir), "/work") for a in mapped]
        flags = [
            "run", "--rm", "-i", "--network", "none" if not granted.network else "bridge", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "64" if limits.allow_fork else "1", "--memory", f"{limits.memory_mb}m",
            "--cpus", "1", "--user", "65532", "--tmpfs", "/tmp:size=64m", "-w", "/work", *mounts,
        ]
        cmd = [self._engine or "docker", *flags, self.image, *mapped]
        return cmd, sha256_hex(" ".join(flags) + sys.platform[:3])
