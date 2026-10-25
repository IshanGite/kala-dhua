"""NFR3.1 graceful degradation, NFR3.2 crash recovery with no duplicate side effect, approval flow inside the executor."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ragnarok.broker.approvals import Broker, UiSession
from ragnarok.core.clock import RealClock, SimClock, set_clock
from ragnarok.harness.executor import ExecContext, Executor
from ragnarok.harness.plan import Plan, PlanNode
from ragnarok.memory.ledger import Ledger
from ragnarok.memory.service import MemoryService
from ragnarok.policy.engine import load_rules
from ragnarok.security.guard import Guard
from ragnarok.skills.registry import SkillRegistry

HUMAN = UiSession(human=True, csrf_ok=True)
SRC = str(Path(__file__).resolve().parents[2] / "src")


def native_skill(root: Path, name: str, perms, *, side_effects="none", dry_run=False, idempotent=True, roles=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "name": name, "description": f"Use when testing {name}.", "parameters": {"type": "object", "properties": {"x": {"type": "string"}, "to": {"type": "array", "items": {"type": "string"}}, "body": {"type": "string"}}},
        "permissions": [{"permission": p} for p in perms], "side_effects": side_effects, "dry_run": dry_run, "idempotent": idempotent,
        "entrypoint": {"type": "native", "action": name}, "param_roles": roles or {}}), encoding="utf-8")
    return d


@pytest.fixture
def lab(tmp_path, keys):
    set_clock(SimClock(1_760_000_000_000))
    mem = MemoryService(tmp_path / "db" / "e.db", keys)
    ledger = Ledger(mem)
    broker = Broker(ledger, keys)
    data = tmp_path / "data"
    for d in ("run", "tmp", "skills"):
        (data / d).mkdir(parents=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    registry = SkillRegistry(ledger, broker, data / "skills")
    src = tmp_path / "src-skills"
    for name, perms, kw in (("t.ok", ["memory.read"], {}), ("t.fail", ["memory.read"], {}), ("t.after", ["memory.read"], {}),
                            ("t.send", ["mail.send"], dict(side_effects="irreversible", dry_run=True, idempotent=False, roles={"recipients": ["to"]})),
                            ("t.read", ["mail.read"], {})):
        registry.register(native_skill(src, name, perms, **kw), builtin=True)
    guard = Guard(registry, ledger, load_rules(), workspace_roots=[ws], run_dir=data / "run", budgets={"task": {"max_node_attempts": 99}, "mail": {"max_recipients": 5}}, contacts={"asha@example.com"})
    sent: list[dict] = []
    approvals: list[dict] = []
    mode = {"decision": "approve"}

    async def ok(params, ctx, *, dry_run):
        return {"value": params.get("x", "ok"), "text": "hello"}

    async def fail(params, ctx, *, dry_run):
        raise RuntimeError("boom")

    async def send(params, ctx, *, dry_run):
        if dry_run:
            return {"would_send_to": params["to"]}
        sent.append(params)
        return {"sent": True}

    async def hook(aid, card):
        approvals.append(card)
        return broker.approve(aid, HUMAN) if mode["decision"] == "approve" else None

    ex = Executor(ledger=ledger, guard=guard, broker=broker, registry=registry, backend=None,
                  natives={"t.ok": ok, "t.fail": fail, "t.after": ok, "t.send": send, "t.read": ok}, approval_hook=hook)
    ctx = ExecContext(compartment="work", workspace=ws, data_dir=data, today="2026-10-25")

    class L:
        pass

    lab = L()
    lab.__dict__.update(dict(mem=mem, ledger=ledger, ex=ex, ctx=ctx, sent=sent, approvals=approvals, mode=mode))
    yield lab
    mem.close()
    set_clock(RealClock())


def N(i, skill, params=None, deps=(), crit=None, on_failure="abort_branch"):
    return PlanNode(id=i, goal=f"do {i}", skill=skill, params=params or {}, depends_on=list(deps), criteria=crit or [{"kind": "ok"}], on_failure=on_failure)


async def test_NFR3_1_one_failure_blocks_only_its_dependents_and_the_task_ends_partial(lab):
    plan = Plan(nodes=[N("a", "t.ok"), N("b", "t.fail"), N("c", "t.after", deps=["b"]), N("d", "t.ok", deps=["a"])])
    tid = lab.ex.start_task("multi-branch", plan, lab.ctx)
    out = await lab.ex.run_task(tid, lab.ctx)
    assert out.state == "partial", "a task with a failure is never reported as succeeded"
    assert out.node_states == {"a": "succeeded", "b": "failed", "c": "blocked", "d": "succeeded"}
    assert out.errors["b"].startswith("E_NATIVE_RUNTIMEERROR") and out.errors["c"] == "E_DEPENDENCY_FAILED"


async def test_NFR3_1_skip_policy_lets_dependents_continue(lab):
    plan = Plan(nodes=[N("b", "t.fail", on_failure="skip"), N("c", "t.ok", deps=["b"])])
    out = await lab.ex.run_task(lab.ex.start_task("skip", plan, lab.ctx), lab.ctx)
    assert out.node_states["b"] == "skipped" and out.node_states["c"] == "succeeded"


async def test_results_flow_between_steps_through_references(lab):
    plan = Plan(nodes=[N("a", "t.ok", {"x": "from-a"}), N("b", "t.ok", {"x": "{ref:a.value}"}, deps=["a"], crit=[{"kind": "equals", "path": "value", "value": "from-a"}])])
    out = await lab.ex.run_task(lab.ex.start_task("refs", plan, lab.ctx), lab.ctx)
    assert out.state == "succeeded" and out.results["b"]["value"] == "from-a"


async def test_criteria_not_met_fails_the_node_never_reports_success(lab):
    plan = Plan(nodes=[N("a", "t.ok", crit=[{"kind": "equals", "path": "value", "value": "something-else"}])])
    out = await lab.ex.run_task(lab.ex.start_task("crit", plan, lab.ctx), lab.ctx)
    assert out.state == "failed" and out.errors["a"] == "E_CRITERIA_NOT_MET"


async def test_NFR4_3_external_write_waits_for_a_click_shows_a_dry_run_and_sends_once(lab):
    plan = Plan(nodes=[N("s", "t.send", {"to": ["asha@example.com"], "body": "Meeting moved"}, crit=[{"kind": "field_present", "path": "sent"}])])
    out = await lab.ex.run_task(lab.ex.start_task("send", plan, lab.ctx), lab.ctx)
    assert out.state == "succeeded" and len(lab.sent) == 1
    card = lab.approvals[0]
    assert card["skill"] == "t.send" and card["dry_run"] == {"would_send_to": ["asha@example.com"]}


async def test_NFR4_3_denied_approval_blocks_the_node_and_nothing_is_sent(lab):
    lab.mode["decision"] = "deny"
    plan = Plan(nodes=[N("s", "t.send", {"to": ["asha@example.com"], "body": "x"}, crit=[{"kind": "field_present", "path": "sent"}]), N("o", "t.ok")])
    out = await lab.ex.run_task(lab.ex.start_task("deny", plan, lab.ctx), lab.ctx)
    assert lab.sent == [] and out.node_states["s"] == "blocked" and out.errors["s"] == "E_APPROVAL_DENIED"
    assert out.node_states["o"] == "succeeded" and out.state == "partial"


async def test_SEC_16_untrusted_ancestry_forces_a_click_and_shows_the_warning(lab):
    # t.read reads mail, so its result is tainted 'received'; anything built from it needs approval with a warning
    plan = Plan(nodes=[N("r", "t.read"), N("w", "t.send", {"to": ["asha@example.com"], "body": "{ref:r.text}"}, deps=["r"], crit=[{"kind": "field_present", "path": "sent"}])])
    out = await lab.ex.run_task(lab.ex.start_task("taint", plan, lab.ctx), lab.ctx)
    assert out.state == "succeeded"
    card = lab.approvals[0]
    assert card["tainted"] is True and any("untrusted" in w.lower() for w in card["warnings"])


async def test_exfiltration_style_bulk_send_is_denied_outright(lab):
    plan = Plan(nodes=[N("s", "t.send", {"to": [f"p{i}@evil.example" for i in range(8)], "body": "invoices"}, crit=[{"kind": "ok"}])])
    out = await lab.ex.run_task(lab.ex.start_task("bulk", plan, lab.ctx), lab.ctx)
    assert lab.sent == [] and out.state == "failed" and out.errors["s"] == "E_DENY"


async def test_replaying_the_same_task_never_repeats_a_confirmed_side_effect(lab):
    plan = Plan(nodes=[N("s", "t.send", {"to": ["asha@example.com"], "body": "once"}, crit=[{"kind": "field_present", "path": "sent"}])])
    tid = lab.ex.start_task("idem", plan, lab.ctx)
    await lab.ex.run_task(tid, lab.ctx)
    for n in lab.ledger.nodes_of(tid):
        lab.ledger.set_node_state(n["id"], "pending")  # simulate a resume that re-queues the node
    out = await lab.ex.run_task(tid, lab.ctx)
    assert out.state == "succeeded" and len(lab.sent) == 1


# ---------------------------------------------------------------------------- NFR3.2: real crashes in a real subprocess
def _run(home: Path, phase: str, failpoint: str | None = None):
    env = {**os.environ, "PYTHONPATH": SRC, "PYTHONUTF8": "1", "RAGNAROK_SECRETS": "memory"}
    env.pop("RAGNAROK_FAILPOINT", None)
    if failpoint:
        env["RAGNAROK_FAILPOINT"] = failpoint
    return subprocess.run([sys.executable, "-m", "ragnarok.evidence.crashlab", str(home), phase], env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("failpoint", ["after_intent_logged", "after_effect_performed"])
def test_NFR3_2_sigkill_matrix_resumes_under_5s_with_no_duplicate_send(tmp_path, failpoint):
    home = tmp_path / "crashlab"
    crashed = _run(home, "first", failpoint)
    assert crashed.returncode == 137, crashed.stderr[-500:]
    rec = _run(home, "recover")
    assert rec.returncode == 0, rec.stderr[-800:]
    report = json.loads(rec.stdout.strip().splitlines()[-1])
    assert report["outbox_lines"] == 1, f"the message must be sent exactly once, got {report['outbox_lines']}"
    assert report["states"] == ["succeeded"]
    assert report["restore_ms"] < 5000


def test_NFR3_2_a_clean_run_sends_exactly_once(tmp_path):
    home = tmp_path / "clean"
    assert _run(home, "first").returncode == 0
    report = json.loads(_run(home, "recover").stdout.strip().splitlines()[-1])
    assert report["outbox_lines"] == 1
