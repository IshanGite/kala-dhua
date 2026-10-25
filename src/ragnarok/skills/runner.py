"""Run a registered skill inside the sandbox (FR2.3). JSON on stdin, JSON on stdout, structured errors, output scanned.

Order on every run: registry hash check (D-29) -> parameter schema check -> sandbox backend (fail closed: no backend, no run)
-> output parse -> output scan (secrets masked) -> run folder wiped.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from ragnarok.config.paths import SUBDIRS
from ragnarok.core.errors import SandboxUnavailable
from ragnarok.core.ids import new_id
from ragnarok.gateway_scan.scan import scan
from ragnarok.sandbox.contract import Granted, Limits, SandboxBackend, run
from ragnarok.sandbox.seatbelt import skill_interpreter
from ragnarok.skills.registry import SkillRegistry


@dataclass
class RunContext:
    workspace: Path
    data_dir: Path
    today: str
    dry_run: bool = False


@dataclass
class SkillResult:
    ok: bool
    result: Any = None
    error_code: str | None = None
    retryable: bool = False
    backend: str = ""
    policy_hash: str = ""
    duration_ms: int = 0
    flags: list[str] = field(default_factory=list)


def data_deny_paths(data_dir: Path) -> list[Path]:
    """Everything in the data directory except tmp/ (where run folders live) and skills/ (a skill reads its own code)."""
    return [data_dir / d for d in SUBDIRS if d not in ("tmp", "skills")]


def run_skill(backend: SandboxBackend | None, registry: SkillRegistry, skill_id: str, params: dict[str, Any], ctx: RunContext) -> SkillResult:
    reg = registry.verify_runnable(skill_id)
    m = reg.manifest
    if m.entrypoint.type != "python":
        return SkillResult(False, error_code="E_NOT_SANDBOXABLE")
    try:
        jsonschema.validate(params, m.parameters)
    except jsonschema.ValidationError as exc:
        return SkillResult(False, error_code="E_PARAMS_INVALID", retryable=True, flags=[str(exc.validator)])
    if backend is None:
        raise SandboxUnavailable("no sandbox backend is available: skill code does not run")
    run_dir = ctx.data_dir / "tmp" / f"run-{new_id()}"
    perms = set(m.permission_names)
    granted = Granted(
        run_dir=run_dir,
        skill_dir=reg.path,
        read_paths=[ctx.workspace] if perms & {"fs.read", "fs.write"} else [],
        write_paths=[ctx.workspace] if "fs.write" in perms or "note.write" in perms else [],
        deny_paths=data_deny_paths(ctx.data_dir),
    )
    limits = Limits(timeout_s=m.timeout_s, memory_mb=m.memory_mb, allow_fork=False)
    stdin = json.dumps({"params": params, "ctx": {"workspace": str(ctx.workspace), "today": ctx.today, "dry_run": ctx.dry_run}}).encode("utf-8")
    argv = [str(skill_interpreter()), "-I", str(reg.path / m.entrypoint.file)]
    try:
        res = run(backend, argv, granted, limits, stdin=stdin)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    base = {"backend": res.backend, "policy_hash": res.policy_hash, "duration_ms": res.duration_ms}
    if res.killed_reason:
        return SkillResult(False, error_code=f"E_KILLED_{res.killed_reason.upper()}", retryable=False, **base)
    try:
        out = json.loads(res.stdout.decode("utf-8", errors="replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return SkillResult(False, error_code="E_SKILL_BAD_OUTPUT", **base)
    if not out.get("ok"):
        err = out.get("error", {})
        return SkillResult(False, error_code=err.get("code", "E_SKILL_ERROR"), retryable=bool(err.get("retryable")), **base)
    if m.returns:
        try:
            jsonschema.validate(out.get("result"), m.returns)
        except jsonschema.ValidationError:
            return SkillResult(False, error_code="E_RESULT_SCHEMA", **base)
    scanned = scan(json.dumps(out.get("result"), ensure_ascii=False), "ingest")
    flags = sorted(scanned.kinds() & {"injection", "hidden_text", "credential", "aws_key", "github_token", "private_key", "otp", "card_number"})
    result = json.loads(scanned.text) if scanned.findings and flags else out.get("result")
    return SkillResult(True, result, flags=flags, **base)
