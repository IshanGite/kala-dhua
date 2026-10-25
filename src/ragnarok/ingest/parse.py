"""Run the parser worker as a subprocess with a timeout and a clean environment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ragnarok.core.errors import RagnarokError

# Phase 4 replaces this with the sandbox backend's wrapper (no network, one input file).
CommandWrapper = Callable[[list[str], Path], list[str]]


def _identity(cmd: list[str], _path: Path) -> list[str]:
    return cmd


class ParseError(RagnarokError):
    code = "E_PARSE"


_ENV_KEEP = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV")


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP}
    env["PYTHONUTF8"] = "1"
    # Never depend on an editable-install .pth file (it is hidden, and so skipped, on macOS: docs/adr/002).
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    return env


def parse_file(path: Path, *, timeout: float = 45.0, wrapper: CommandWrapper = _identity) -> dict[str, Any]:
    cmd = wrapper([sys.executable, "-m", "ragnarok.ingest.parse_worker", str(path)], path)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=clean_env(), check=False)  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        raise ParseError("parser timed out", code="E_PARSE_TIMEOUT") from exc
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ParseError("parser crashed", code="E_PARSE_CRASH") from exc
    if not data.get("ok"):
        raise ParseError("parse failed", code=data.get("code", "E_PARSE"))
    return data
