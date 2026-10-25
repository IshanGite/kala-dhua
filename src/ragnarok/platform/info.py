"""Machine facts and hardware profile selection (apple-16, win-nvidia, win-cpu, low-ram)."""

from __future__ import annotations

import platform as _platform
import shutil
import sys
from dataclasses import asdict, dataclass

import psutil


@dataclass(frozen=True)
class MachineInfo:
    os: str
    os_version: str
    arch: str
    cpu_count: int
    ram_gb: float
    has_nvidia: bool
    python: str

    def as_dict(self) -> dict:
        return asdict(self)


def machine_info() -> MachineInfo:
    return MachineInfo(
        os={"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform),
        os_version=_platform.platform(),
        arch=_platform.machine(),
        cpu_count=psutil.cpu_count(logical=True) or 1,
        ram_gb=round(psutil.virtual_memory().total / 2**30, 1),
        has_nvidia=shutil.which("nvidia-smi") is not None,
        python=_platform.python_version(),
    )


def select_profile(info: MachineInfo) -> str:
    if info.ram_gb < 10:
        return "low-ram"
    if info.os == "macos" and info.arch == "arm64":
        return "apple-16"
    if info.has_nvidia:
        return "win-nvidia"
    return "win-cpu"
