"""Backend selection: probe the backends for this platform, run the suite, grade the best one (ARCHITECTURE 9).

macOS: seatbelt, then container. Windows: appcontainer, then container, then restricted. The grade (full / restricted / none)
is printed everywhere and recorded in every evidence report, so a claim about isolation always names the backend that earned it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from ragnarok.sandbox.container import ContainerBackend
from ragnarok.sandbox.contract import Grade, SandboxBackend
from ragnarok.sandbox.devunsafe import DevUnsafeBackend
from ragnarok.sandbox.seatbelt import SeatbeltBackend
from ragnarok.sandbox.suite.cases import CaseResult, grade_results, run_suite
from ragnarok.sandbox.windows import AppContainerBackend, RestrictedBackend


@dataclass
class Selection:
    backend: SandboxBackend | None
    grade: Grade
    results: list[CaseResult]
    tried: list[str]

    @property
    def name(self) -> str:
        return self.backend.name if self.backend else "none"


def candidates() -> list[SandboxBackend]:
    if sys.platform == "darwin":
        return [SeatbeltBackend(), ContainerBackend(), DevUnsafeBackend()]
    if sys.platform == "win32":
        return [AppContainerBackend(), ContainerBackend(), RestrictedBackend(), DevUnsafeBackend()]
    return [ContainerBackend(), DevUnsafeBackend()]


def select_backend(*, full: bool = False) -> Selection:
    """Return the first available backend with the best grade. `full` runs all cases; otherwise a 4-case self-test."""
    tried: list[str] = []
    best: Selection | None = None
    for b in candidates():
        probe = b.probe()
        tried.append(f"{b.name}:{'ok' if probe.available else probe.detail}")
        if not probe.available:
            continue
        results = run_suite(b, quick=not full)
        sel = Selection(b, grade_results(results), results, tried)
        if sel.grade == Grade.FULL:
            return sel
        if best is None:
            best = sel
    return best or Selection(None, Grade.NONE, [], tried)
