"""Crash lab: a self-contained crash-recovery scenario (NFR3.2, chaos tests, evidence report).

    python -m ragnarok.evidence.crashlab <home> first      # runs a 3-step task; an armed failpoint kills it abruptly (exit 137)
    python -m ragnarok.evidence.crashlab <home> recover    # reopens the same state, recovers, prints a JSON report

The task sends one message through a native, side-effecting action that appends to <home>/outbox.txt. After a crash at ANY point
the message must appear exactly once. A test arms RAGNAROK_FAILPOINT and checks the outbox and the recovery time.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from ragnarok.broker.approvals import Broker, UiSession
from ragnarok.harness.executor import ExecContext, Executor
from ragnarok.harness.plan import Plan, PlanNode
from ragnarok.harness.recovery import recover
from ragnarok.memory.ledger import Ledger
from ragnarok.memory.service import MemoryService
from ragnarok.policy.engine import load_rules
from ragnarok.security.guard import Guard
from ragnarok.skills.registry import SkillRegistry
from ragnarok.store.crypto import KeyHierarchy

MANIFEST = {
    "name": "crash.send", "version": "0.1.0", "description": "Use when a test must send a message once.",
    "parameters": {"type": "object", "properties": {"to": {"type": "array", "items": {"type": "string"}}, "body": {"type": "string"}}, "required": ["to", "body"]},
    "permissions": [{"permission": "mail.send"}], "side_effects": "irreversible", "dry_run": True, "idempotent": False,
    "entrypoint": {"type": "native", "action": "crash.send"}, "param_roles": {"recipients": ["to"]},
}
KEYS = KeyHierarchy(bytes(range(32)))
HUMAN = UiSession(human=True, csrf_ok=True)


def build(home: Path):
    for d in ("db", "run", "tmp", "skills", "workspace"):
        (home / d).mkdir(parents=True, exist_ok=True)
    mem = MemoryService(home / "db" / "lab.db", KEYS)
    ledger = Ledger(mem)
    broker = Broker(ledger, KEYS)
    registry = SkillRegistry(ledger, broker, home / "skills")
    src = home / "skill-src" / "crash.send"
    src.mkdir(parents=True, exist_ok=True)
    (src / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8", newline="\n")
    registry.register(src, builtin=True)
    guard = Guard(registry, ledger, load_rules(), workspace_roots=[home / "workspace"], run_dir=home / "run",
                  budgets={"task": {"max_node_attempts": 50}, "mail": {"max_recipients": 5}}, contacts={"asha@example.com"})

    async def send(params, ctx, *, dry_run):
        if dry_run:
            return {"would_send_to": params["to"]}
        with (home / "outbox.txt").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{params['to'][0]}|{params['body']}\n")
        return {"sent": True, "to": params["to"]}

    async def verify(params, ctx, *, dry_run):
        box = home / "outbox.txt"
        line = f"{params['to'][0]}|{params['body']}"
        return {"performed": (line in box.read_text(encoding="utf-8").splitlines()) if box.exists() else False}

    async def approve(aid, card):
        return broker.approve(aid, HUMAN)

    ex = Executor(ledger=ledger, guard=guard, broker=broker, registry=registry, backend=None, natives={"crash.send": send, "crash.send.verify": verify}, approval_hook=approve)
    ctx = ExecContext(compartment="work", workspace=home / "workspace", data_dir=home, today="2026-10-25")
    return mem, ledger, broker, ex, ctx


def plan() -> Plan:
    return Plan(nodes=[PlanNode(id="send", goal="send the update", skill="crash.send", params={"to": ["asha@example.com"], "body": "Meeting moved to 4pm"},
                                criteria=[{"kind": "field_present", "path": "sent"}])])


async def first(home: Path) -> None:
    mem, ledger, broker, ex, ctx = build(home)
    tid = ex.start_task("send the update", plan(), ctx)
    await ex.run_task(tid, ctx)
    print(json.dumps({"phase": "first", "finished": True}))
    mem.close()


async def recover_phase(home: Path) -> None:
    mem, ledger, broker, ex, ctx = build(home)
    t0 = time.perf_counter()
    report = await recover(ledger, ex, broker, lambda t: ctx, run=False)
    restore_ms = (time.perf_counter() - t0) * 1000
    outcomes = [await ex.run_task(t, ctx) for t in report.resumed]
    outbox = (home / "outbox.txt").read_text(encoding="utf-8").splitlines() if (home / "outbox.txt").exists() else []
    print(json.dumps({"phase": "recover", "restore_ms": round(restore_ms, 1), "resumed": len(report.resumed), "requeued": report.requeued_nodes,
                      "states": [o.state for o in outcomes], "outbox_lines": len(outbox)}))
    mem.close()


if __name__ == "__main__":
    home_dir, which = Path(sys.argv[1]), sys.argv[2]
    asyncio.run(first(home_dir) if which == "first" else recover_phase(home_dir))
