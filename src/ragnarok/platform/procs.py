"""Process control: the only place with OS-specific process calls (lint rule 3).

Spawn a child in its own process group and kill a whole process tree. `psutil` does the portable part: it walks children
recursively and kills them, which behaves the same on macOS and Windows (TerminateProcess). Windows Job Objects with
kill-on-close are added in sandbox/appcontainer.py for stronger guarantees.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import psutil


def popen_group(cmd: list[str], *, cwd: str | None, env: dict[str, str], stdin: Any = subprocess.PIPE) -> subprocess.Popen:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(  # noqa: S603
        cmd, cwd=cwd, env=env, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, **kwargs
    )


def kill_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = root.children(recursive=True) + [root]
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=3)


def tree_rss_bytes(pid: int) -> int:
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0
    total = 0
    for p in procs:
        try:
            total += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return total
