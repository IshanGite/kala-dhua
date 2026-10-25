"""Execution ledger: tasks, nodes, effects, approvals, decisions and budget counters.

Lives under memory/ because it writes SQL; everything goes through MemoryService.with_tx so each change is its own
committed transaction (NFR3.2). Side effects use intent -> performed -> confirmed with an idempotency key, so a crash
never repeats an action.
"""

from __future__ import annotations

import json
from typing import Any

from ragnarok.core.clock import get_clock
from ragnarok.core.ids import new_id
from ragnarok.memory.service import MemoryService

TERMINAL_TASK = ("succeeded", "partial", "failed", "cancelled")
TERMINAL_NODE = ("succeeded", "failed", "skipped", "blocked")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class Ledger:
    def __init__(self, mem: MemoryService) -> None:
        self._m = mem

    def _now(self) -> int:
        return get_clock().now_ms()

    # ------------------------------------------------------------------ tasks and nodes
    def create_task(self, compartment: str, goal: str, trigger: str, trace_id: str, plan: dict | None = None) -> str:
        comp = self._m.ensure_compartment(compartment)
        tid, now = new_id(), self._now()
        self._m.with_tx(
            lambda c: c.execute(
                "INSERT INTO tasks(id,compartment_id,goal,trigger_kind,state,plan_json,working_json,trace_id,created_at,updated_at)"
                " VALUES (?,?,?,?,'created',?, '{}', ?,?,?)",
                (tid, comp, goal, trigger, _dumps(plan) if plan else None, trace_id, now, now),
            )
        )
        return tid

    def set_task_state(self, task_id: str, state: str, *, working: dict | None = None, plan: dict | None = None) -> None:
        def op(c):
            c.execute("UPDATE tasks SET state=?, updated_at=? WHERE id=?", (state, self._now(), task_id))
            if working is not None:
                c.execute("UPDATE tasks SET working_json=? WHERE id=?", (_dumps(working), task_id))
            if plan is not None:
                c.execute("UPDATE tasks SET plan_json=? WHERE id=?", (_dumps(plan), task_id))

        self._m.with_tx(op)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        def op(c):
            r = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(r) if r else None

        return self._m.with_tx(op)

    def non_terminal_tasks(self) -> list[dict[str, Any]]:
        ph = ",".join("?" * len(TERMINAL_TASK))
        return self._m.with_tx(lambda c: [dict(r) for r in c.execute(f"SELECT * FROM tasks WHERE state NOT IN ({ph}) ORDER BY created_at", TERMINAL_TASK)])

    def add_node(self, task_id: str, ord_: int, skill_id: str, params: dict, *, depends_on: list[str] | None = None,
                 criteria: list[Any] | None = None, min_trust: str = "user_direct", tier: int = 0, node_id: str | None = None) -> str:
        nid = node_id or new_id()
        self._m.with_tx(
            lambda c: c.execute(
                "INSERT INTO task_nodes(id,task_id,ord,skill_id,params_json,depends_on_json,criteria_json,state,min_trust,tier,updated_at)"
                " VALUES (?,?,?,?,?,?,?,'pending',?,?,?)",
                (nid, task_id, ord_, skill_id, _dumps(params), _dumps(depends_on or []), _dumps(criteria or []), min_trust, tier, self._now()),
            )
        )
        return nid

    def set_node_state(self, node_id: str, state: str, *, result: Any = None, error_code: str | None = None, attempt: bool = False) -> None:
        def op(c):
            c.execute("UPDATE task_nodes SET state=?, updated_at=? WHERE id=?", (state, self._now(), node_id))
            if result is not None:
                c.execute("UPDATE task_nodes SET result_json=? WHERE id=?", (_dumps(result), node_id))
            if error_code is not None:
                c.execute("UPDATE task_nodes SET error_code=? WHERE id=?", (error_code, node_id))
            if attempt:
                c.execute("UPDATE task_nodes SET attempts=attempts+1 WHERE id=?", (node_id,))

        self._m.with_tx(op)

    def nodes_of(self, task_id: str) -> list[dict[str, Any]]:
        return self._m.with_tx(lambda c: [dict(r) for r in c.execute("SELECT * FROM task_nodes WHERE task_id=? ORDER BY ord", (task_id,))])

    # ------------------------------------------------------------------ effects (idempotent side effects)
    def log_intent(self, key: str, node_id: str, intent: dict) -> bool:
        """False if an effect with this key already exists (a retry must not repeat it)."""

        def op(c) -> bool:
            return c.execute("INSERT OR IGNORE INTO effects(key,task_node_id,intent_json,status,created_at) VALUES (?,?,?,'intent',?)",
                             (key, node_id, _dumps(intent), self._now())).rowcount == 1

        return bool(self._m.with_tx(op))

    def set_effect(self, key: str, status: str) -> None:
        def op(c):
            c.execute("UPDATE effects SET status=?, confirmed_at=? WHERE key=?", (status, self._now() if status == "confirmed" else None, key))

        self._m.with_tx(op)

    def get_effect(self, key: str) -> dict[str, Any] | None:
        def op(c):
            r = c.execute("SELECT * FROM effects WHERE key=?", (key,)).fetchone()
            return dict(r) if r else None

        return self._m.with_tx(op)

    def effects_in_state(self, status: str) -> list[dict[str, Any]]:
        return self._m.with_tx(lambda c: [dict(r) for r in c.execute("SELECT * FROM effects WHERE status=?", (status,))])

    # ------------------------------------------------------------------ approvals
    def create_approval(self, node_id: str, skill_id: str, params_hash: str, card: dict, warnings: list[str], expires_at: int) -> str:
        aid = new_id()
        self._m.with_tx(
            lambda c: c.execute(
                "INSERT INTO approvals(id,task_node_id,skill_id,params_hash,status,warnings_json,card_json,created_at,expires_at)"
                " VALUES (?,?,?,?,'pending',?,?,?,?)",
                (aid, node_id, skill_id, params_hash, _dumps(warnings), _dumps(card), self._now(), expires_at),
            )
        )
        return aid

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        def op(c):
            r = c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            return dict(r) if r else None

        return self._m.with_tx(op)

    def update_approval(self, approval_id: str, **fields: Any) -> None:
        allowed = {"status", "params_hash", "card_json", "decided_at", "token_hash", "expires_at"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update approval fields {bad}")
        sets = ",".join(f"{k}=?" for k in fields)
        self._m.with_tx(lambda c: c.execute(f"UPDATE approvals SET {sets} WHERE id=?", (*fields.values(), approval_id)))

    def pending_approvals(self) -> list[dict[str, Any]]:
        return self._m.with_tx(lambda c: [dict(r) for r in c.execute("SELECT * FROM approvals WHERE status='pending' ORDER BY created_at")])

    def consume_approval_atomically(self, approval_id: str, token_hash: str) -> bool:
        """Single use: only one caller can flip approved -> used with a matching token."""

        def op(c) -> bool:
            return c.execute("UPDATE approvals SET status='used' WHERE id=? AND status='approved' AND token_hash=? AND expires_at>?",
                             (approval_id, token_hash, self._now())).rowcount == 1

        return bool(self._m.with_tx(op))

    # ------------------------------------------------------------------ decisions (explainability, NFR4.1)
    def record_decision(self, kind: str, *, task_id: str | None = None, node_id: str | None = None, tier: str | None = None,
                        backend: str | None = None, inputs: Any = None, candidates: Any = None, choice: Any = None,
                        rationale: str | None = None, outcome: Any = None, latency_ms: float | None = None, model_id: str | None = None) -> str:
        did = new_id()
        self._m.with_tx(
            lambda c: c.execute(
                "INSERT INTO decisions(id,task_id,task_node_id,ts,kind,tier,backend,inputs_json,candidates_json,choice_json,rationale,outcome_json,latency_ms,model_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (did, task_id, node_id, self._now(), kind, tier, backend, _dumps(inputs or {}), _dumps(candidates or []), _dumps(choice or {}),
                 rationale, _dumps(outcome or {}), latency_ms, model_id),
            )
        )
        return did

    def decisions_of(self, task_id: str) -> list[dict[str, Any]]:
        return self._m.with_tx(lambda c: [dict(r) for r in c.execute("SELECT * FROM decisions WHERE task_id=? ORDER BY ts", (task_id,))])

    # ------------------------------------------------------------------ budgets
    def bump(self, principal: str, counter: str, window_ms: int, n: int = 1) -> int:
        """Increment a per-window counter and return the new total (rate limits and budgets)."""
        start = (self._now() // window_ms) * window_ms

        def op(c) -> int:
            c.execute("INSERT INTO budgets_usage(principal,window_start,counter,n) VALUES (?,?,?,?) ON CONFLICT(principal,window_start,counter) DO UPDATE SET n=n+?",
                      (principal, start, counter, n, n))
            return int(c.execute("SELECT n FROM budgets_usage WHERE principal=? AND window_start=? AND counter=?", (principal, start, counter)).fetchone()[0])

        return self._m.with_tx(op)

    def usage(self, principal: str, counter: str, window_ms: int) -> int:
        start = (self._now() // window_ms) * window_ms

        def op(c) -> int:
            r = c.execute("SELECT n FROM budgets_usage WHERE principal=? AND window_start=? AND counter=?", (principal, start, counter)).fetchone()
            return int(r[0]) if r else 0

        return self._m.with_tx(op)
