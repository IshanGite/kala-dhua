"""Windows sandbox backends: TODO for Tushar (see docs/HANDOVER_TUSHAR.md).

Two backends, strongest first. Both must pass the same suite (sandbox/suite) plus the six Windows-specific cases.

* AppContainerBackend: start the process with an AppContainer identity that has NO capabilities (so no network) and grant that
  identity access to the run folder only, plus a Job Object for limits (memory, active processes, CPU time, kill-on-close).
  Win32 through ctypes: CreateAppContainerProfile / DeriveAppContainerSidFromAppContainerName, a PROC_THREAD_ATTRIBUTE_LIST
  with SECURITY_CAPABILITIES, an ACE on the run folder for the AppContainer SID. To verify in the spike: whether profile creation
  needs administrator rights, and that loopback is blocked by default (it is, which is what we want for skills).
* RestrictedBackend: a Job Object plus a restricted token at low integrity. Weak read isolation, so its grade is RESTRICTED at best:
  only first-party low-risk skills may run under it.

Until implemented, probe() reports unavailable and select_backend() falls back to the container backend or refuses to run skills
(fail closed). Do not stub a pass.
"""

from __future__ import annotations

import sys

from ragnarok.core.errors import SandboxUnavailable
from ragnarok.sandbox.contract import Granted, Limits, ProbeResult


class AppContainerBackend:
    name = "appcontainer"

    def probe(self) -> ProbeResult:
        return ProbeResult(self.name, False, "not implemented yet" if sys.platform == "win32" else "not Windows")

    def wrap(self, argv: list[str], granted: Granted, limits: Limits) -> tuple[list[str], str]:
        raise SandboxUnavailable("appcontainer backend is not implemented yet")


class RestrictedBackend:
    name = "restricted"

    def probe(self) -> ProbeResult:
        return ProbeResult(self.name, False, "not implemented yet" if sys.platform == "win32" else "not Windows")

    def wrap(self, argv: list[str], granted: Granted, limits: Limits) -> tuple[list[str], str]:
        raise SandboxUnavailable("restricted backend is not implemented yet")
