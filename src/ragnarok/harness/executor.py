"""Durable executor (FR3, NFR3.1, NFR3.2).

* Every state transition is its own committed transaction BEFORE the next action starts.
* Side effects use intent -> performed -> confirmed with an idempotency key, so a crash never repeats an action.
* An approval suspends a NODE, not the task: independent branches keep running.
* A failing node blocks only its dependents; the task ends `partial`, never `succeeded`, and says why.
* All permission decisions come from the Guard; the model's plan is only a proposal.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ragnarok.broker.approvals import Broker
from ragnarok.core.errors import SandboxUnavailable
from ragnarok.core.failpoints import failpoint
from ragnarok.core.ids import new_id, stable_key
from ragnarok.core.logging import log
from ragnarok.harness.plan import REF, Plan
from ragnarok.harness.verify import all_passed, dig, evaluate
from ragnarok.memory.ledger import Ledger
from ragnarok.policy.taint import lowest
from ragnarok.sandbox.contract import SandboxBackend
from ragnarok.security.guard import Guard, ToolCall
from ragnarok.skills.registry import SkillNotRunnable, SkillRegistry
from ragnarok.skills.runner import RunContext, run_skill

READ_SOURCES = {"mail.read": "received", "chat.read": "received", "drive.read": "received", "fs.read": "user_file"}
MAX_CONCURRENCY = 2


class NativeHandler(Protocol):
    async def __call__(self, params: dict[str, Any], ctx: ExecContext, *, dry_run: bool) -> dict[str, Any]: ...


ApprovalHook = Callable[[str, dict[str, Any]], Awaitable[str | None]]  # (approval_id, card) -> approval token, or None if denied


@dataclass
class ExecContext:
    compartment: str
    workspace: Path
    data_dir: Path
    today: str
    mode: str = "draft"
    base_trust: str = "user_direct"
    has_untrusted_input: bool = False


@dataclass
class TaskOutcome:
    task_id: str
    state: str  # succeeded | partial | failed
    node_states: dict[str, str]
    errors: dict[str, str] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)


def resolve_refs(value: Any, results: dict[str, Any]) -> Any:
    if isinstance(value, str):
        m = REF.fullmatch(value)
        if m:
            return dig(results.get(m.group(1)), m.group(2)) if m.group(2) else results.get(m.group(1))
        return REF.sub(lambda mm: str(dig(results.get(mm.group(1)), mm.group(2)) if mm.group(2) else results.get(mm.group(1))), value)
    if isinstance(value, dict):
        return {k: resolve_refs(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_refs(v, results) for v in value]
    return value


class Executor:
    def __init__(self, *, ledger: Ledger, guard: Guard, broker: Broker, registry: SkillRegistry, backend: SandboxBackend | None,
                 natives: dict[str, NativeHandler] | None = None, approval_hook: ApprovalHook | None = None) -> None:
        self._l, self._g, self._b, self._reg, self._backend = ledger, guard, broker, registry, backend
        self._natives = natives or {}
        self._hook = approval_hook

    # ------------------------------------------------------------------ start
    def start_task(self, goal: str, plan: Plan, ctx: ExecContext, trigger: str = "user", *, plan_meta: dict | None = None) -> str:
        trace = new_id()[:12]
        tid = self._l.create_task(ctx.compartment, goal, trigger, trace, plan={**plan.model_dump(), **(plan_meta or {})})
        for i, n in enumerate(plan.nodes):
            m = self._reg.verify_runnable(n.skill).manifest
            self._l.add_node(tid, i, n.skill, n.params, depends_on=n.depends_on, criteria=n.criteria, min_trust=ctx.base_trust,
                             tier=int(m.tier), node_id=f"{tid[:8]}-{n.id}")
        self._l.set_task_state(tid, "running")
        return tid

    async def run_task(self, task_id: str, ctx: ExecContext) -> TaskOutcome:
        plan = Plan.model_validate({k: v for k, v in json.loads(self._l.get_task(task_id)["plan_json"]).items() if k in ("nodes", "assumptions")})
        on_failure = {f"{task_id[:8]}-{n.id}": n.on_failure for n in plan.nodes}
        results: dict[str, Any] = {}
        trust: dict[str, str] = {}
        errors: dict[str, str] = {}
        prefix = f"{task_id[:8]}-"
        while True:
            nodes = {n["id"]: n for n in self._l.nodes_of(task_id)}
            for nid, n in nodes.items():
                if n["state"] == "succeeded" and n["result_json"]:
                    results[nid[len(prefix):]] = json.loads(n["result_json"])
            self._block_dependents(task_id, nodes, on_failure)
            nodes = {n["id"]: n for n in self._l.nodes_of(task_id)}
            ready = [n for n in nodes.values() if n["state"] == "pending" and all(nodes[d]["state"] in ("succeeded", "skipped") for d in self._deps(n, prefix))]
            running = [n for n in nodes.values() if n["state"] in ("running", "awaiting_approval")]
            if not ready and not running:
                break
            batch = ready[:MAX_CONCURRENCY]
            if not batch:
                await asyncio.sleep(0.05)
                continue
            outs = await asyncio.gather(*[self._run_node(task_id, n, ctx, results, trust, prefix) for n in batch], return_exceptions=True)
            for n, o in zip(batch, outs, strict=True):
                if isinstance(o, BaseException):  # contain it: never let one node crash the task
                    self._l.set_node_state(n["id"], "failed", error_code="E_NODE_EXCEPTION")
                    errors[n["id"][len(prefix):]] = "E_NODE_EXCEPTION"
        final = {n["id"][len(prefix):]: n["state"] for n in self._l.nodes_of(task_id)}
        for n in self._l.nodes_of(task_id):
            if n["error_code"]:
                errors[n["id"][len(prefix):]] = n["error_code"]
        ok = [s for s in final.values() if s == "succeeded"]
        state = "succeeded" if final and all(s in ("succeeded", "skipped") for s in final.values()) else ("partial" if ok else "failed")
        self._l.set_task_state(task_id, state)
        self._l.record_decision("task_finished", task_id=task_id, outcome={"state": state, "errors": errors})
        return TaskOutcome(task_id, state, final, errors, results)

    @staticmethod
    def _deps(node: dict, prefix: str) -> list[str]:
        return [prefix + d for d in json.loads(node["depends_on_json"])]

    def _block_dependents(self, task_id: str, nodes: dict[str, dict], on_failure: dict[str, str]) -> None:
        changed = True
        prefix = f"{task_id[:8]}-"
        while changed:
            changed = False
            for n in nodes.values():
                if n["state"] != "pending":
                    continue
                for d in self._deps(n, prefix):
                    ds = nodes[d]["state"]
                    if ds == "failed" and on_failure.get(d) == "skip":
                        self._l.set_node_state(d, "skipped")
                        nodes[d]["state"] = "skipped"
                        changed = True
                    elif ds in ("failed", "blocked"):
                        self._l.set_node_state(n["id"], "blocked", error_code="E_DEPENDENCY_FAILED")
                        n["state"] = "blocked"
                        changed = True
                        break

    # ------------------------------------------------------------------ one node
    def _node_trust(self, node: dict, results_trust: dict[str, str], ctx: ExecContext, prefix: str) -> str:
        levels = [ctx.base_trust, *[results_trust[d[len(prefix):]] for d in self._deps(node, prefix) if d[len(prefix):] in results_trust]]
        return lowest(levels)

    async def _run_node(self, task_id: str, node: dict, ctx: ExecContext, results: dict[str, Any], trust: dict[str, str], prefix: str) -> None:
        nid, short = node["id"], node["id"][len(prefix):]
        skill = node["skill_id"]
        self._l.set_node_state(nid, "running", attempt=True)
        params = resolve_refs(json.loads(node["params_json"]), results)
        node_trust = self._node_trust(node, trust, ctx, prefix)
        try:
            reg = self._reg.verify_runnable(skill)
        except SkillNotRunnable as exc:
            self._l.set_node_state(nid, "failed", error_code=exc.code)
            return
        manifest = reg.manifest
        verdict = self._g.check(ToolCall(node_id=nid, task_id=task_id, skill_id=skill, params=params, min_trust=node_trust, mode=ctx.mode,
                                         has_untrusted_input=ctx.has_untrusted_input or node_trust in ("received", "web")))
        if verdict.action in ("deny", "panic"):
            self._l.set_node_state(nid, "failed", error_code=verdict.code or "E_DENIED")
            return
        token: str | None = None
        if verdict.action in ("require_approval", "require_stepup", "quarantine"):
            self._l.set_node_state(nid, "awaiting_approval")
            dry = await self._dry_run(manifest, skill, params, ctx) if manifest.dry_run else None
            aid = self._b.create_request(nid, skill, params, tier=max(verdict.tier, 2) if verdict.action != "require_stepup" else 3,
                                         warnings=verdict.warnings, trust=node_trust, dry_run=dry, task_goal=json.dumps(self._l.get_task(task_id)["goal"]))
            token = await self._hook(aid, self._b.card(aid)) if self._hook else None
            if token is None:
                self._l.set_node_state(nid, "blocked", error_code="E_APPROVAL_DENIED")
                return
            self._l.set_node_state(nid, "running")
        # ---- the effect protocol for anything that changes the world
        effect_key = None
        if manifest.side_effects != "none" and not ctx.mode == "observe":
            effect_key = stable_key(task_id, nid, verdict.params_hash)
            fresh = self._l.log_intent(effect_key, nid, {"skill": skill, "params_hash": verdict.params_hash})
            failpoint("after_intent_logged")
            if not fresh:
                replay = await self._replay_effect(effect_key, nid, manifest, node, ctx, params)
                if replay == "done":
                    node_row = next(n for n in self._l.nodes_of(task_id) if n["id"] == nid)
                    results[short] = json.loads(node_row["result_json"])
                    trust[short] = self._result_trust(manifest)
                    return
                if replay == "stop":
                    return
        if token is not None and not self._b.verify_at_execution(token, params):
            self._l.set_node_state(nid, "blocked", error_code="E_APPROVAL_INVALID")
            return
        outcome = await self._execute(manifest, skill, params, ctx)
        if not outcome["ok"]:
            if effect_key:
                self._l.set_effect(effect_key, "failed")
            self._l.set_node_state(nid, "failed", error_code=outcome["code"])
            return
        result = outcome["result"]
        self._l.set_node_state(nid, "running", result=result)  # result persisted BEFORE the effect is marked performed
        if effect_key:
            self._l.set_effect(effect_key, "performed")
        failpoint("after_effect_performed")
        crits = evaluate(json.loads(node["criteria_json"]), result, workspace=ctx.workspace, readback=True if manifest.side_effects != "none" else None)
        if not all_passed(crits):
            self._l.set_node_state(nid, "failed", error_code="E_CRITERIA_NOT_MET")
            return
        if effect_key:
            self._l.set_effect(effect_key, "confirmed")
        results[short] = result
        trust[short] = self._result_trust(manifest)
        self._l.set_node_state(nid, "succeeded")
        log("node_done", node=nid[:12], skill=skill)

    async def _replay_effect(self, key: str, nid: str, manifest: Any, node: dict, ctx: ExecContext, params: dict[str, Any]) -> str:
        """An effect with this key already exists (a crash or a retry). Never repeat it blindly. Returns done | stop | rerun."""
        existing = self._l.get_effect(key)
        row = next(n for n in self._l.nodes_of(node["task_id"]) if n["id"] == nid)
        if existing is None:
            return "rerun"
        if existing["status"] in ("performed", "confirmed") and row["result_json"]:
            if existing["status"] == "performed":
                crits = evaluate(json.loads(node["criteria_json"]), json.loads(row["result_json"]), workspace=ctx.workspace, readback=True)
                if not all_passed(crits):
                    self._l.set_node_state(nid, "failed", error_code="E_CRITERIA_NOT_MET")
                    return "stop"
                self._l.set_effect(key, "confirmed")
            self._l.set_node_state(nid, "succeeded")
            return "done"
        if existing["status"] == "failed":
            self._l.set_effect(key, "intent")
            return "rerun"
        if existing["status"] == "intent" and not manifest.idempotent:
            verdict = await self._read_back(manifest, params, ctx)
            if verdict is None:
                # The action may or may not have happened and we cannot read it back: ask the user instead of risking a duplicate.
                self._l.set_node_state(nid, "blocked", error_code="E_NEEDS_USER_VERIFY")
                return "stop"
            if verdict:  # it did happen before the crash: record it, never repeat it
                self._l.set_node_state(nid, "running", result={"verified_by_read_back": True, "sent": True})
                self._l.set_effect(key, "performed")
                return await self._replay_effect(key, nid, manifest, node, ctx, params)
        return "rerun"

    async def _read_back(self, manifest: Any, params: dict[str, Any], ctx: ExecContext) -> bool | None:
        """Ask the system the action changed whether it happened. None means: unknown (no verifier)."""
        verifier = self._natives.get((manifest.entrypoint.action or manifest.name) + ".verify")
        if verifier is None:
            return None
        try:
            r = await verifier(params, ctx, dry_run=False)
        except Exception:  # noqa: BLE001
            return None
        return None if r.get("performed") is None else bool(r["performed"])

    @staticmethod
    def _result_trust(manifest: Any) -> str:
        for p in manifest.permission_names:
            if p in READ_SOURCES and READ_SOURCES[p] == "received":
                return "received"
        return "user_file" if "fs.read" in manifest.permission_names else "agent"

    async def _dry_run(self, manifest: Any, skill: str, params: dict[str, Any], ctx: ExecContext) -> dict[str, Any] | None:
        try:
            out = await self._execute(manifest, skill, params, ctx, dry_run=True)
        except Exception:  # noqa: BLE001
            return None
        return out.get("result") if out["ok"] else {"dry_run_failed": out["code"]}

    async def _execute(self, manifest: Any, skill: str, params: dict[str, Any], ctx: ExecContext, *, dry_run: bool = False) -> dict[str, Any]:
        if manifest.entrypoint.type == "native":
            handler = self._natives.get(manifest.entrypoint.action or skill)
            if handler is None:
                return {"ok": False, "code": "E_NO_NATIVE_HANDLER"}
            try:
                return {"ok": True, "result": await handler(params, ctx, dry_run=dry_run)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "code": f"E_NATIVE_{type(exc).__name__.upper()}"}
        rc = RunContext(workspace=ctx.workspace, data_dir=ctx.data_dir, today=ctx.today, dry_run=dry_run)
        try:
            res = await asyncio.to_thread(run_skill, self._backend, self._reg, skill, params, rc)
        except SandboxUnavailable:
            return {"ok": False, "code": "E_SANDBOX_UNAVAILABLE"}
        return {"ok": True, "result": res.result} if res.ok else {"ok": False, "code": res.error_code or "E_SKILL_ERROR"}


__all__ = ["ExecContext", "Executor", "NativeHandler", "TaskOutcome", "resolve_refs"]
