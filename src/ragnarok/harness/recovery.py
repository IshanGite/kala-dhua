"""Boot recovery (NFR3.2): load every non-terminal task, put interrupted nodes back, expire stale approvals, resume the executor.

The effect log makes resuming safe: a node whose side effect was already performed or confirmed is NOT executed again
(Executor._replay_effect), and a node whose effect state is unknowable is blocked with a question for the user.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ragnarok.broker.approvals import Broker
from ragnarok.harness.executor import ExecContext, Executor, TaskOutcome
from ragnarok.memory.ledger import Ledger


@dataclass
class RecoveryReport:
    restore_ms: float  # process start of recovery to "executor resumed" (the NFR3.2 number)
    resumed: list[str] = field(default_factory=list)
    requeued_nodes: int = 0
    expired_approvals: int = 0
    outcomes: list[TaskOutcome] = field(default_factory=list)


async def recover(ledger: Ledger, executor: Executor, broker: Broker, ctx_for: Callable[[dict], ExecContext], *, run: bool = True) -> RecoveryReport:
    started = time.perf_counter()
    report = RecoveryReport(0.0)
    report.expired_approvals = broker.expire_stale()
    tasks = ledger.non_terminal_tasks()
    for t in tasks:
        for n in ledger.nodes_of(t["id"]):
            if n["state"] in ("running", "awaiting_approval"):
                ledger.set_node_state(n["id"], "pending")
                report.requeued_nodes += 1
        report.resumed.append(t["id"])
    report.restore_ms = (time.perf_counter() - started) * 1000
    if run:
        for t in tasks:
            report.outcomes.append(await executor.run_task(t["id"], ctx_for(t)))
    return report
