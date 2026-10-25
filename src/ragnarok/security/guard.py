"""The Guard: every tool call passes ONE function that runs all the checks (SEC-01, ARCHITECTURE 5.3).

Order: panic flag -> may this principal execute? -> registered, enabled, hash-verified skill (D-29) -> parameter schema ->
budgets -> content scan of parameters (secrets, canaries) -> policy engine with taint -> autonomy mode. The result tells the executor
whether to run, ask for a click, ask for a step-up, or refuse. The model never decides any of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from ragnarok.broker.approvals import params_hash
from ragnarok.broker.autonomy import blocked_by_mode
from ragnarok.core.errors import PolicyDenied
from ragnarok.gateway_scan.scan import scan
from ragnarok.memory.ledger import Ledger
from ragnarok.policy.engine import Decision, Rule, Signal, Snapshot, decide
from ragnarok.security import panic
from ragnarok.security.canary import CanaryManager
from ragnarok.security.identities import assert_can_execute
from ragnarok.skills.manifest import Manifest
from ragnarok.skills.registry import SkillNotRunnable, SkillRegistry

HOUR_MS = 3_600_000
FIFTEEN_MIN_MS = 900_000
HOURLY_COUNTER = {"mail.send": "mail_send", "chat.post": "chat_posts", "calendar.write": "calendar_writes", "fs.write": "fs_mutations", "fs.delete": "fs_mutations", "note.add": "fs_mutations"}


@dataclass
class ToolCall:
    node_id: str
    task_id: str
    skill_id: str
    params: dict[str, Any]
    min_trust: str = "user_direct"
    principal: str = "executor"
    mode: str = "draft"
    is_prepare: bool = False
    has_untrusted_input: bool = False
    has_private_data: bool = True


@dataclass
class GuardResult:
    action: str  # allow | require_approval | require_stepup | quarantine | deny | panic
    code: str | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    params_hash: str = ""
    tier: int = 0
    retryable: bool = False

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _strings(v)]
    return []


def _role_values(manifest: Manifest, params: dict[str, Any], role: str) -> list[Any]:
    out: list[Any] = []
    for name in manifest.param_roles.get(role, []):
        v = params.get(name)
        if isinstance(v, list):
            out.extend(v)
        elif v is not None:
            out.append(v)
    return out


class Guard:
    def __init__(self, registry: SkillRegistry, ledger: Ledger, rules: list[Rule], *, workspace_roots: list[Path], run_dir: Path,
                 budgets: dict[str, Any], contacts: set[str] | None = None, canary: CanaryManager | None = None) -> None:
        self._reg, self._l, self._rules = registry, ledger, rules
        self._roots, self._run_dir, self._b = [str(p) for p in workspace_roots], run_dir, budgets
        self._contacts = contacts or set()
        self._canary = canary

    def _deny(self, call: ToolCall, code: str, why: str, *, retryable: bool = False, tier: int = 0, phash: str = "") -> GuardResult:
        self._l.record_decision("guard_block", task_id=call.task_id, node_id=call.node_id, choice={"code": code}, rationale=why)
        return GuardResult("deny", code, [code], [why], phash, tier, retryable)

    def check(self, call: ToolCall) -> GuardResult:
        phash = params_hash(call.params)
        if panic.is_panicked(self._run_dir):
            return self._deny(call, "E_PANIC", "The system is in panic mode", phash=phash)
        try:
            assert_can_execute(call.principal)
        except PolicyDenied as exc:
            return self._deny(call, exc.code, "This actor may not execute tools", phash=phash)
        try:
            reg = self._reg.verify_runnable(call.skill_id)
        except SkillNotRunnable as exc:
            return self._deny(call, exc.code, str(exc), phash=phash)
        m = reg.manifest
        try:
            jsonschema.validate(call.params, m.parameters)
        except jsonschema.ValidationError as exc:
            return self._deny(call, "E_PARAMS_INVALID", f"Parameters do not match the skill: {exc.message[:120]}", retryable=True, tier=int(m.tier), phash=phash)

        task_b = self._b.get("task", {})
        attempts = self._l.bump(f"task:{call.task_id}", "node_attempts", FIFTEEN_MIN_MS)
        if attempts > task_b.get("max_node_attempts", 40):
            return self._deny(call, "E_BUDGET_TASK", "This task used its attempt budget", tier=int(m.tier), phash=phash)
        counter = HOURLY_COUNTER.get(call.skill_id)
        if counter:
            limit = self._b.get("hourly", {}).get(counter)
            if limit is not None and self._l.bump("user", counter, HOUR_MS) > limit:
                return self._deny(call, "E_BUDGET_HOURLY", f"Hourly limit for {counter} reached", tier=int(m.tier), phash=phash)

        for text in _strings(call.params):
            if self._canary and self._canary.check_text(text, self._run_dir, call.task_id):
                return GuardResult("panic", "E_CANARY", ["R-CANARY-01"], ["A decoy value appeared in an action"], phash, int(m.tier))
            res = scan(text, "draft")
            if res.blocked:
                return self._deny(call, "E_SECRET_IN_PARAMS", "The action contains what looks like a secret", tier=int(m.tier), phash=phash)

        recipients = [str(x) for x in _role_values(m, call.params, "recipients")]
        paths = [str(x) for x in _role_values(m, call.params, "paths")]
        paths = [p if Path(p).is_absolute() else str(Path(self._roots[0]) / p) for p in paths] if self._roots else paths
        count_vals = _role_values(m, call.params, "count")
        count = int(count_vals[0]) if count_vals and str(count_vals[0]).isdigit() else len(paths)
        mail_max = self._b.get("mail", {}).get("max_recipients", 5)
        sig = Signal(skill=m.name, tier=int(m.tier), min_trust=call.min_trust, permissions=m.permission_names, recipients=recipients, paths=paths,
                     hosts=list(m.hosts), count=count, external_write=m.external_write)
        snap = Snapshot(contacts=self._contacts, workspace_roots=self._roots, allowed_hosts=list(m.hosts),
                        has_untrusted_input=call.has_untrusted_input, has_private_data=call.has_private_data)
        d: Decision = decide(snap, sig, self._rules)
        if len(recipients) > mail_max and d.action != "deny":
            d = Decision("deny", (*d.rules, "R-SEND-02"), (*d.warnings, "Too many recipients"))
        if blocked_by_mode(call.mode, int(m.tier)):
            return self._deny(call, "E_MODE", f"This connector is in {call.mode} mode", tier=int(m.tier), phash=phash)
        self._l.record_decision("guard_check", task_id=call.task_id, node_id=call.node_id, choice={"action": d.action, "rules": list(d.rules)},
                                inputs={"skill": m.name, "tier": int(m.tier), "min_trust": call.min_trust})
        return GuardResult(d.action, None if d.action in ("allow", "require_approval", "require_stepup") else f"E_{d.action.upper()}", list(d.rules),
                           list(d.warnings), phash, int(m.tier))
