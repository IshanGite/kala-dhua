"""`ragnarok doctor`: the sync tool. Run after every pull, on every machine. Exit code 1 if anything is red."""

from __future__ import annotations

import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import psutil

from ragnarok.config.paths import data_dir
from ragnarok.platform.info import machine_info, select_profile

OLLAMA = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str = ""


def check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) == (3, 12)
    return Check("python", "ok" if ok else "fail", f"{v.major}.{v.minor}.{v.micro} (need 3.12)")


def check_ollama() -> list[Check]:
    out: list[Check] = []
    try:
        version = httpx.get(f"{OLLAMA}/api/version", timeout=2, trust_env=False).json().get("version", "?")
        out.append(Check("ollama", "ok", f"reachable on 127.0.0.1:11434, version {version}"))
    except Exception as exc:  # noqa: BLE001
        return [Check("ollama", "fail", f"not reachable on loopback ({type(exc).__name__}); start it first")]
    try:
        names = {m["name"] for m in httpx.get(f"{OLLAMA}/api/tags", timeout=3, trust_env=False).json().get("models", [])}
        from ragnarok.llm_gateway.models import ModelLock

        lock = ModelLock.load(Path("models.lock"))
        if not lock.entries:
            out.append(Check("models.lock", "warn", "missing; run `ragnarok setup` after models are pulled"))
        else:
            missing = [e.name for e in lock.entries.values() if e.name not in names and f"{e.name}:latest" not in names]
            out.append(Check("models", "fail" if missing else "ok", f"missing: {missing}" if missing else f"{len(lock.entries)} locked models present"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("models", "warn", f"could not list models ({type(exc).__name__})"))
    out.append(check_ollama_lockdown())
    return out


def check_ollama_lockdown() -> Check:
    host_env = os.environ.get("OLLAMA_HOST", "")
    if host_env and not host_env.startswith(("127.", "localhost", "http://127.", "http://localhost")):
        return Check("ollama-lockdown", "fail", f"OLLAMA_HOST={host_env!r} is not loopback")
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))  # TEST-NET address: picks the LAN interface, sends nothing
        lan = probe.getsockname()[0]
        probe.close()
    except OSError:
        return Check("ollama-lockdown", "ok", "no LAN address to probe (offline)")
    if lan.startswith("127."):
        return Check("ollama-lockdown", "ok", "no LAN address to probe")
    try:
        socket.create_connection((lan, 11434), timeout=1).close()
        return Check("ollama-lockdown", "fail", f"Ollama is reachable on the LAN address {lan}; bind it to 127.0.0.1")
    except OSError:
        return Check("ollama-lockdown", "ok", "not reachable on the LAN address")


def check_store() -> Check:
    try:
        from ragnarok.store.selftest import run

        ok, detail = run()
        return Check("encrypted-store", "ok" if ok else "fail", detail)
    except Exception as exc:  # noqa: BLE001
        return Check("encrypted-store", "fail", f"{type(exc).__name__}: {exc}")


def check_secret_store() -> Check:
    try:
        from ragnarok.platform.secrets import KeyringStore

        s = KeyringStore(service="ragnarok-doctor")
        s.set("probe", "x" * 2000)
        ok = s.get("probe") == "x" * 2000
        s.delete("probe")
        return Check("secret-store", "ok" if ok else "fail", "read, write, delete, long values (chunked)" if ok else "round trip failed")
    except Exception as exc:  # noqa: BLE001
        return Check("secret-store", "fail", f"{type(exc).__name__}: {exc}")


def check_hygiene() -> Check:
    from ragnarok.hygiene import scan

    root = Path.cwd()
    if not (root / ".git").exists():
        return Check("repo-hygiene", "warn", "not run inside the repository")
    findings = scan(root)
    if findings:
        return Check("repo-hygiene", "fail", "; ".join(f"{f.path}: {f.problem}" for f in findings[:5]))
    return Check("repo-hygiene", "ok", "no reserved names, case collisions, CRLF or long paths")


def check_resources() -> Check:
    free_gb = psutil.disk_usage(str(data_dir().anchor or "/")).free / 2**30
    ram = psutil.virtual_memory().total / 2**30
    status = "ok" if free_gb > 15 and ram >= 8 else "warn"
    return Check("resources", status, f"{ram:.0f} GB RAM, {free_gb:.0f} GB disk free")


def check_laya() -> Check:
    try:
        import importlib.util

        found = importlib.util.find_spec("laya") is not None
    except Exception:  # noqa: BLE001
        found = False
    return Check("laya", "ok" if found else "warn", "installed" if found else "not installed (optional: uv sync --extra laya)")


def write_profile(profile: str) -> None:
    info = machine_info()
    lines = [f'profile = "{profile}"', "", "[machine]"]
    for k, v in info.as_dict().items():
        lines.append(f'{k} = "{v}"' if isinstance(v, str) else f"{k} = {str(v).lower() if isinstance(v, bool) else v}")
    Path("profile.toml").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def run_doctor(*, benchmark: bool = False, restore_test: bool = False) -> int:
    checks: list[Check] = [check_python(), *check_ollama(), check_store(), check_secret_store(), check_laya(), check_resources(), check_hygiene()]
    mark = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]"}
    for c in checks:
        print(f"{mark[c.status]} {c.name}: {c.detail}")
    info = machine_info()
    profile = select_profile(info)
    print(f"profile: {profile} ({info.os} {info.arch}, {info.ram_gb} GB RAM)")
    if benchmark:
        write_profile(profile)
        print("profile.toml written")
    return 1 if any(c.status == "fail" for c in checks) else 0
