"""Persistence for the route library, router examples, calibration and route events (lives under memory/ because it writes SQL)."""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from ragnarok.core.clock import get_clock
from ragnarok.core.ids import new_id
from ragnarok.memory.service import MemoryService, blob_to_vec, vec_to_blob

ROUTE_FIELDS = {"state", "shadow_agree", "successes", "failures", "demotions", "last_used", "pinned", "tree_hash", "recipe_json", "signature_text"}


class RoutesStore:
    def __init__(self, mem: MemoryService) -> None:
        self._m = mem

    def _now(self) -> int:
        return get_clock().now_ms()

    def insert(self, *, compartment: str, signature_text: str, intent_family: str, recipe: dict, risk_tier_hint: int, trust_floor: str,
               tree_hash: str, created_from_task: str | None, state: str = "candidate") -> str:
        rid, comp = new_id(), self._m.ensure_compartment(compartment)
        self._m.with_tx(lambda c: c.execute(
            "INSERT INTO routes(id,compartment_id,signature_text,intent_family,recipe_json,risk_tier_hint,trust_floor,state,created_at,created_from_task,tree_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, comp, signature_text, intent_family, json.dumps(recipe, sort_keys=True), risk_tier_hint, trust_floor, state, self._now(), created_from_task, tree_hash)))
        return rid

    def get(self, rid: str) -> dict[str, Any] | None:
        def op(c):
            r = c.execute("SELECT r.*, k.name AS compartment FROM routes r JOIN compartments k ON k.id=r.compartment_id WHERE r.id=?", (rid,)).fetchone()
            return dict(r) if r else None

        return self._m.with_tx(op)

    def list(self, *, state: str | None = None, compartment: str | None = None) -> list[dict[str, Any]]:
        def op(c):
            sql, args = "SELECT r.*, k.name AS compartment FROM routes r JOIN compartments k ON k.id=r.compartment_id WHERE 1=1", []
            if state:
                sql += " AND r.state=?"
                args.append(state)
            if compartment:
                sql += " AND k.name=?"
                args.append(compartment)
            return [dict(r) for r in c.execute(sql + " ORDER BY r.created_at", args)]

        return self._m.with_tx(op)

    def update(self, rid: str, **fields: Any) -> None:
        bad = set(fields) - ROUTE_FIELDS
        if bad:
            raise ValueError(f"cannot update route fields {bad}")
        sets = ",".join(f"{k}=?" for k in fields)
        self._m.with_tx(lambda c: c.execute(f"UPDATE routes SET {sets} WHERE id=?", (*fields.values(), rid)))

    def delete(self, rid: str) -> None:
        def op(c):
            c.execute("DELETE FROM route_embeddings WHERE route_id=?", (rid,))
            c.execute("DELETE FROM route_events WHERE route_id=?", (rid,))
            c.execute("DELETE FROM routes WHERE id=?", (rid,))

        self._m.with_tx(op)

    def put_embedding(self, rid: str, model_id: str, vec: np.ndarray) -> None:
        self._m.with_tx(lambda c: c.execute("INSERT OR REPLACE INTO route_embeddings(route_id,model_id,dim,vec) VALUES (?,?,?,?)", (rid, model_id, int(vec.shape[0]), vec_to_blob(vec))))

    def embeddings(self, states: tuple[str, ...]) -> list[tuple[str, str, str, np.ndarray]]:
        """(route_id, compartment name, state, vector) for routes in the given states."""
        ph = ",".join("?" * len(states))

        def op(c):
            return [(r[0], r[1], r[2], blob_to_vec(r[3])) for r in c.execute(
                f"SELECT e.route_id, k.name, r.state, e.vec FROM route_embeddings e JOIN routes r ON r.id=e.route_id JOIN compartments k ON k.id=r.compartment_id WHERE r.state IN ({ph})", states)]

        return self._m.with_tx(op)

    # ------------------------------------------------------------------ events (learning curve) and examples (Laya training data)
    def add_event(self, route_id: str | None, kind: str, *, tier: str | None = None, latency_ms: float | None = None, ok: bool | None = None, trace_id: str | None = None) -> None:
        self._m.with_tx(lambda c: c.execute("INSERT INTO route_events(id,route_id,ts,kind,tier,latency_ms,ok,trace_id) VALUES (?,?,?,?,?,?,?,?)",
                                            (new_id(), route_id, self._now(), kind, tier, latency_ms, None if ok is None else int(ok), trace_id)))

    def events(self, *, kind: str | None = None, route_id: str | None = None) -> list[dict[str, Any]]:
        def op(c):
            sql, args = "SELECT * FROM route_events WHERE 1=1", []
            if kind:
                sql += " AND kind=?"
                args.append(kind)
            if route_id:
                sql += " AND route_id=?"
                args.append(route_id)
            return [dict(r) for r in c.execute(sql + " ORDER BY ts, rowid", args)]

        return self._m.with_tx(op)

    def add_example(self, query_text: str, labels: dict, *, family: str | None, skill: str | None, plan_needed: bool | None, risk: str | None, source: str, trust: str) -> str:
        eid = new_id()
        self._m.with_tx(lambda c: c.execute(
            "INSERT INTO router_examples(id,query_text,family,skill,plan_needed,risk,labels_json,source,trust,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid, query_text, family, skill, None if plan_needed is None else int(plan_needed), risk, json.dumps(labels, sort_keys=True), source, trust, self._now())))
        return eid

    def examples(self, limit: int = 1000) -> list[dict[str, Any]]:
        return self._m.with_tx(lambda c: [dict(r) for r in c.execute("SELECT * FROM router_examples ORDER BY created_at LIMIT ?", (limit,))])

    def save_calibration(self, decision: str, backend: str, temperature: float, threshold: float, n: int) -> None:
        self._m.with_tx(lambda c: c.execute("INSERT OR REPLACE INTO router_calibration(decision,backend,temperature,threshold,fitted_at,n) VALUES (?,?,?,?,?,?)",
                                            (decision, backend, temperature, threshold, self._now(), n)))

    def latest_calibration(self) -> dict[tuple[str, str], tuple[float, float]]:
        def op(c):
            out: dict[tuple[str, str], tuple[float, float]] = {}
            for r in c.execute("SELECT decision, backend, temperature, threshold FROM router_calibration ORDER BY fitted_at"):
                out[(r[0], r[1])] = (r[2], r[3])
            return out

        return self._m.with_tx(op)
