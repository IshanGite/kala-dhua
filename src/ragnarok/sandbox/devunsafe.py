"""dev-unsafe: no isolation at all. Only with RAGNAROK_ENV=dev and RAGNAROK_UNSAFE_DEV=1, shows a banner, and is REFUSED in
acceptance runs (D-19, CLAUDE.md prohibitions)."""

from __future__ import annotations

import os

from ragnarok.core.errors import SandboxUnavailable
from ragnarok.core.logging import log
from ragnarok.sandbox.contract import Granted, Limits, ProbeResult


class DevUnsafeBackend:
    name = "dev-unsafe"

    def _enabled(self) -> bool:
        return os.environ.get("RAGNAROK_ENV") == "dev" and os.environ.get("RAGNAROK_UNSAFE_DEV") == "1"

    def probe(self) -> ProbeResult:
        if os.environ.get("RAGNAROK_ACCEPTANCE") == "1":
            return ProbeResult(self.name, False, "refused in acceptance runs")
        return ProbeResult(self.name, self._enabled(), "UNSAFE: no isolation" if self._enabled() else "needs RAGNAROK_ENV=dev and RAGNAROK_UNSAFE_DEV=1")

    def wrap(self, argv: list[str], granted: Granted, limits: Limits) -> tuple[list[str], str]:
        if not self.probe().available:
            raise SandboxUnavailable("dev-unsafe backend is not enabled")
        log("UNSAFE_DEV_SANDBOX_ACTIVE")
        return list(argv), "dev-unsafe"
