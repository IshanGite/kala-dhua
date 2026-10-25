"""The SandboxBackend contract. Every backend must provide: no network by default, filesystem limits, resource limits,
whole-tree kill, a clean environment, output caps, a policy report, and FAIL CLOSED if the policy cannot be applied."""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ragnarok.platform.procs import kill_tree, popen_group, tree_rss_bytes


class Grade(enum.StrEnum):
    FULL = "full"  # every suite case contained: any skill may run
    RESTRICTED = "restricted"  # some case failed: only first-party low-risk skills may run
    NONE = "none"  # no usable backend: no skill code runs


@dataclass(frozen=True)
class Limits:
    timeout_s: float = 30.0
    memory_mb: int = 256
    max_output_bytes: int = 1_000_000
    allow_fork: bool = False


@dataclass
class Granted:
    """What this run may touch beyond its own run folder."""

    run_dir: Path
    read_paths: list[Path] = field(default_factory=list)
    write_paths: list[Path] = field(default_factory=list)
    skill_dir: Path | None = None
    deny_paths: list[Path] = field(default_factory=list)  # always denied (the data directory, credential folders)
    network: bool = False
    env: dict[str, str] = field(default_factory=dict)  # extra allowlisted variables


@dataclass
class RunResult:
    ok: bool
    returncode: int | None
    stdout: bytes
    stderr: bytes
    killed_reason: str | None  # None | timeout | memory | output
    duration_ms: int
    backend: str
    policy_hash: str


@dataclass
class ProbeResult:
    backend: str
    available: bool
    detail: str = ""


class SandboxBackend(Protocol):
    name: str

    def probe(self) -> ProbeResult: ...

    def wrap(self, argv: list[str], granted: Granted, limits: Limits) -> tuple[list[str], str]:
        """Return (wrapped command, policy hash). Raise SandboxUnavailable if the policy cannot be applied (fail closed)."""
        ...


SAFE_ENV_KEYS = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL")


def clean_env(granted: Granted) -> dict[str, str]:
    """Allowlisted environment only: no secrets, no tokens, no inherited credentials."""
    import os

    env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = str(granted.run_dir)
    env.update(granted.env)
    return env


def supervise(cmd: list[str], *, cwd: Path, env: dict[str, str], stdin: bytes | None, limits: Limits, backend: str, policy_hash: str) -> RunResult:
    """Run a wrapped command with a wall-clock timeout, a memory monitor and output caps; kill the whole tree on any breach."""
    started = time.monotonic()
    proc = popen_group(cmd, cwd=str(cwd), env=env)
    out, err = bytearray(), bytearray()
    killed: list[str] = []
    cap = limits.max_output_bytes

    def pump(stream, sink: bytearray) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            if len(sink) < cap:
                sink.extend(chunk[: cap - len(sink)])
            if len(sink) >= cap and not killed:
                killed.append("output")
                kill_tree(proc.pid)
                return

    def feed() -> None:
        try:
            if stdin:
                proc.stdin.write(stdin)
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    threads = [threading.Thread(target=pump, args=(proc.stdout, out), daemon=True), threading.Thread(target=pump, args=(proc.stderr, err), daemon=True),
               threading.Thread(target=feed, daemon=True)]
    for t in threads:
        t.start()
    limit_bytes = limits.memory_mb * 1024 * 1024
    while proc.poll() is None:
        if time.monotonic() - started > limits.timeout_s:
            killed.append("timeout")
            kill_tree(proc.pid)
            break
        if tree_rss_bytes(proc.pid) > limit_bytes:
            killed.append("memory")
            kill_tree(proc.pid)
            break
        if killed:
            break
        time.sleep(0.03)
    kill_tree(proc.pid)  # make sure no descendant outlives the run
    try:
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        pass
    for t in threads[:2]:
        t.join(timeout=2)
    reason = killed[0] if killed else None
    rc = proc.returncode
    return RunResult(ok=(reason is None and rc == 0), returncode=rc, stdout=bytes(out), stderr=bytes(err), killed_reason=reason,
                     duration_ms=int((time.monotonic() - started) * 1000), backend=backend, policy_hash=policy_hash)


def run(backend: SandboxBackend, argv: list[str], granted: Granted, limits: Limits, *, stdin: bytes | None = None) -> RunResult:
    granted.run_dir.mkdir(parents=True, exist_ok=True)
    cmd, policy_hash = backend.wrap(argv, granted, limits)
    try:
        return supervise(cmd, cwd=granted.run_dir, env=clean_env(granted), stdin=stdin, limits=limits, backend=backend.name, policy_hash=policy_hash)
    finally:
        cleanup = getattr(backend, "cleanup", None)
        if cleanup:
            cleanup()
