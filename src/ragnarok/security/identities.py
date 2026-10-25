"""Non-human identities (SEC-04): every actor has a principal with a permission set. Model roles can NEVER execute.

Only the executor (acting for the user or a trigger) calls the broker and guard; routers, arbiters, planners and extractors
return data and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from ragnarok.core.errors import PolicyDenied


@dataclass(frozen=True)
class Principal:
    id: str
    kind: str  # user | router | arbiter | planner | extractor | skill | connector | executor
    can_execute: bool = False
    permissions: tuple[str, ...] = ()


BUILTIN = {
    "user": Principal("user", "user", True, ("*",)),
    "executor": Principal("executor", "executor", True, ("skill.run",)),
    "router": Principal("router", "router", False, ("decide",)),
    "arbiter": Principal("arbiter", "arbiter", False, ("decide", "extract")),
    "planner": Principal("planner", "planner", False, ("plan",)),
    "extractor": Principal("extractor", "extractor", False, ("extract",)),
}


def principal(pid: str) -> Principal:
    if pid in BUILTIN:
        return BUILTIN[pid]
    kind, _, name = pid.partition(":")
    if kind in ("skill", "connector"):
        return Principal(pid, kind, False, (f"{kind}.{name}",))
    raise PolicyDenied(f"unknown principal {pid!r}", code="E_UNKNOWN_PRINCIPAL")


def assert_can_execute(pid: str) -> None:
    p = principal(pid)
    if not p.can_execute:
        raise PolicyDenied(f"principal {pid!r} may not execute tools", code="E_PRINCIPAL_CANNOT_EXECUTE")
