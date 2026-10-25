"""The 13 malicious-skill cases (FR2.3). Each case is a tiny Python program run INSIDE the backend; the harness plants decoys
and local servers OUTSIDE it and checks whether the attack got through. A case passes when the attack is contained.

Windows-specific cases (junction, UNC path, alternate data stream, 8.3 names, reserved device names, handle inheritance) are
listed in docs/HANDOVER_TUSHAR.md and belong in sandbox/suite/windows_cases.py once the Windows backends exist.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ragnarok.sandbox.contract import Grade, Granted, Limits, RunResult, SandboxBackend, run
from ragnarok.sandbox.seatbelt import skill_interpreter

CANARY = "CANARY-DECOY-DO-NOT-READ-7f3a91"


@dataclass(frozen=True)
class CaseResult:
    name: str
    passed: bool
    detail: str = ""


class Env:
    """Decoys and servers that live outside the sandbox."""

    def __init__(self) -> None:
        self.root = Path(os.path.realpath(tempfile.mkdtemp(prefix="rag-suite-")))
        self.home = self.root / "home"
        self.data = self.root / "ragnarok-data"
        self.run_dir = self.root / "run"
        for d in (self.home / ".ssh", self.home / "Library" / "Keychains", self.data / "db", self.run_dir):
            d.mkdir(parents=True, exist_ok=True)
        (self.home / ".ssh" / "id_rsa").write_text(CANARY, encoding="utf-8")
        (self.home / "Library" / "Keychains" / "login.keychain-db").write_text(CANARY, encoding="utf-8")
        (self.data / "db" / "ragnarok.db").write_text(CANARY, encoding="utf-8")
        self.tcp = socket.socket()
        self.tcp.bind(("127.0.0.1", 0))
        self.tcp.listen(5)
        self.tcp_port = self.tcp.getsockname()[1]
        self.tcp_hits = 0
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.udp.settimeout(0.2)
        self.udp_port = self.udp.getsockname()[1]
        self.udp_hits = 0
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        self.tcp.settimeout(0.2)
        while not self._stop:
            try:
                c, _ = self.tcp.accept()
                self.tcp_hits += 1
                c.close()
            except (TimeoutError, OSError):
                pass
            try:
                self.udp.recvfrom(64)
                self.udp_hits += 1
            except (TimeoutError, OSError):
                pass

    def close(self) -> None:
        self._stop = True
        time.sleep(0.3)
        self.tcp.close()
        self.udp.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def granted(self, **kw) -> Granted:
        return Granted(run_dir=self.run_dir, deny_paths=[self.data, self.home], **kw)

    def script(self, body: str) -> list[str]:
        path = self.run_dir / "case.py"
        path.write_text("import os, sys, socket, subprocess, time\n" + body + "\n", encoding="utf-8", newline="\n")
        return [str(skill_interpreter()), "-I", str(path)]


def _breach(res: RunResult) -> bool:
    return b"BREACH" in res.stdout


def _contained(name: str, res: RunResult, extra_ok: bool = True) -> CaseResult:
    ok = not _breach(res) and extra_ok
    return CaseResult(name, ok, "contained" if ok else f"BREACH: {res.stdout[:80]!r}")


def case_read_ssh_key(b: SandboxBackend, e: Env) -> CaseResult:
    r = run(b, e.script(f"print('BREACH' if open(r'{e.home / '.ssh' / 'id_rsa'}').read() else 'no')"), e.granted(), Limits(timeout_s=10))
    return _contained("read_ssh_key", r)


def case_read_keychain(b: SandboxBackend, e: Env) -> CaseResult:
    r = run(b, e.script(f"print('BREACH' if open(r'{e.home / 'Library' / 'Keychains' / 'login.keychain-db'}').read() else 'no')"), e.granted(), Limits(timeout_s=10))
    return _contained("read_keychain", r)


def case_read_data_dir(b: SandboxBackend, e: Env) -> CaseResult:
    r = run(b, e.script(f"print('BREACH' if open(r'{e.data / 'db' / 'ragnarok.db'}').read() else 'no')"), e.granted(), Limits(timeout_s=10))
    return _contained("read_ragnarok_database", r)


def case_write_home(b: SandboxBackend, e: Env) -> CaseResult:
    target = e.home / "pwned.txt"
    run(b, e.script(f"open(r'{target}','w').write('x')\nprint('BREACH')"), e.granted(), Limits(timeout_s=10))
    return CaseResult("write_home", not target.exists(), "contained" if not target.exists() else "BREACH: file written outside the run folder")


def case_tcp_connect(b: SandboxBackend, e: Env) -> CaseResult:
    before = e.tcp_hits
    run(b, e.script(f"s=socket.create_connection(('127.0.0.1',{e.tcp_port}),timeout=3)\nprint('BREACH')"), e.granted(), Limits(timeout_s=10))
    time.sleep(0.4)
    ok = e.tcp_hits == before
    return CaseResult("outbound_tcp", ok, "contained" if ok else "BREACH: connection reached the server")


def case_udp_dns_like(b: SandboxBackend, e: Env) -> CaseResult:
    before = e.udp_hits
    run(b, e.script(f"s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\ns.sendto(b'q',('127.0.0.1',{e.udp_port}))\nprint('BREACH')"), e.granted(), Limits(timeout_s=10))
    time.sleep(0.5)
    ok = e.udp_hits == before
    return CaseResult("udp_dns_exfil", ok, "contained" if ok else "BREACH: datagram reached the server")


def case_fork_bomb(b: SandboxBackend, e: Env) -> CaseResult:
    marker = e.run_dir / "child_ran.txt"
    res = run(b, e.script(f"subprocess.run([sys.executable,'-I','-c',\"open(r'{marker}','w').write('x')\"])\nprint('BREACH')"), e.granted(), Limits(timeout_s=10))
    return CaseResult("fork_and_spawn", not marker.exists() and not _breach(res), "contained" if not marker.exists() else "BREACH: child process ran")


def case_infinite_loop(b: SandboxBackend, e: Env) -> CaseResult:
    r = run(b, e.script("while True:\n    pass"), e.granted(), Limits(timeout_s=2))
    return CaseResult("infinite_loop", r.killed_reason == "timeout" and r.duration_ms < 8000, f"killed_reason={r.killed_reason} in {r.duration_ms} ms")


def case_memory_hog(b: SandboxBackend, e: Env) -> CaseResult:
    r = run(b, e.script("chunks=[]\nwhile True:\n    chunks.append(bytearray(40*1024*1024))\n    time.sleep(0.02)"), e.granted(), Limits(timeout_s=20, memory_mb=200))
    return CaseResult("memory_hog", r.killed_reason == "memory", f"killed_reason={r.killed_reason}")


def case_output_flood(b: SandboxBackend, e: Env) -> CaseResult:
    r = run(b, e.script("while True:\n    sys.stdout.write('x'*65536)\n    sys.stdout.flush()"), e.granted(), Limits(timeout_s=20, max_output_bytes=500_000))
    ok = r.killed_reason == "output" and len(r.stdout) <= 500_000
    return CaseResult("output_flood", ok, f"killed_reason={r.killed_reason}, {len(r.stdout)} bytes kept")


def case_symlink_escape(b: SandboxBackend, e: Env) -> CaseResult:
    link = e.run_dir / "innocent.txt"
    body = f"os.symlink(r'{e.home / '.ssh' / 'id_rsa'}', r'{link}')\nprint('BREACH' if open(r'{link}').read() else 'no')"
    r = run(b, e.script(body), e.granted(), Limits(timeout_s=10))
    return _contained("symlink_escape", r)


def case_env_leak(b: SandboxBackend, e: Env) -> CaseResult:
    os.environ["RAGNAROK_SUITE_SECRET"] = CANARY
    try:
        r = run(b, e.script("print('BREACH' if 'RAGNAROK_SUITE_SECRET' in os.environ else 'clean')"), e.granted(), Limits(timeout_s=10))
    finally:
        os.environ.pop("RAGNAROK_SUITE_SECRET", None)
    return _contained("environment_leak", r)


def case_child_outlives_timeout(b: SandboxBackend, e: Env) -> CaseResult:
    marker = e.run_dir / "late_child.txt"
    child = f"import time; time.sleep(4); open(r'{marker}','w').write('x')"
    body = f"subprocess.Popen([sys.executable,'-I','-c',{child!r}])\nwhile True:\n    time.sleep(0.1)"
    run(b, e.script(body), e.granted(), Limits(timeout_s=1.5, allow_fork=True))
    time.sleep(5.5)
    return CaseResult("child_outlives_timeout", not marker.exists(), "contained" if not marker.exists() else "BREACH: a child kept running after the kill")


CASES: list[Callable[[SandboxBackend, Env], CaseResult]] = [
    case_read_ssh_key, case_read_keychain, case_read_data_dir, case_write_home, case_tcp_connect, case_udp_dns_like, case_fork_bomb,
    case_infinite_loop, case_memory_hog, case_output_flood, case_symlink_escape, case_env_leak, case_child_outlives_timeout,
]
QUICK = {"read_ssh_key", "outbound_tcp", "write_home", "infinite_loop"}


def run_suite(backend: SandboxBackend, *, quick: bool = False) -> list[CaseResult]:
    env = Env()
    results: list[CaseResult] = []
    try:
        for fn in CASES:
            short = fn.__name__.replace("case_", "")
            if quick and short not in {"read_ssh_key", "tcp_connect", "write_home", "infinite_loop"}:
                continue
            try:
                results.append(fn(backend, env))
            except Exception as exc:  # noqa: BLE001 - a backend that cannot even run a case is not contained
                results.append(CaseResult(short, False, f"backend error: {type(exc).__name__}"))
    finally:
        env.close()
    return results


def grade_results(results: list[CaseResult]) -> Grade:
    if not results:
        return Grade.NONE
    return Grade.FULL if all(r.passed for r in results) else Grade.RESTRICTED


def interpreter_info() -> str:
    return f"{skill_interpreter()} ({sys.version.split()[0]})"
