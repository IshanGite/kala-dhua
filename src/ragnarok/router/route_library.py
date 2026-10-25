"""The route library: the self-improving half of the cascade (ARCHITECTURE 4.4 to 4.6).

A route is a RECIPE (what to run), never permission (whether it may run): approvals and tiers are recomputed at run time from the
skill manifests and the taint of the current inputs. Routes are learned only from fully successful, code-verified, untainted tasks,
verified in shadow mode against the big path, bound to the hashes of the skills they use, and demoted at the first sign of trouble.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from ragnarok.core.ids import sha256_hex
from ragnarok.memory.models import TRUST_RANK
from ragnarok.memory.routes_store import RoutesStore
from ragnarok.router.slots import detect, instantiate_value, signature, templatize

SHADOW_NEEDED = 2
MAX_DEMOTIONS = 3
DUP_SIMILARITY = 0.95
_PERSONAL = ("@",)


@dataclass
class RouteMatch:
    route_id: str
    similarity: float
    margin: float
    state: str
    nodes: list[dict[str, Any]]  # instantiated plan nodes, slots filled
    skills: list[str]


@dataclass
class CandidateResult:
    created: bool
    route_id: str | None = None
    rejected_by: str | None = None


def recipe_hash(recipe: dict[str, Any], skill_hashes: dict[str, str]) -> str:
    skills = sorted({n["skill"] for n in recipe["nodes"]})
    return sha256_hex(json.dumps([recipe, [skill_hashes.get(s, "") for s in skills]], sort_keys=True))


def _walk(value: Any, fn: Callable[[str], Any]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {k: _walk(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    return value


class RouteLibrary:
    def __init__(self, store: RoutesStore, *, tau: float = 0.86, margin: float = 0.04, min_success: float = 0.8,
                 skill_hashes: Callable[[list[str]], dict[str, str]] | None = None) -> None:
        self.store, self.tau, self.margin, self.min_success = store, tau, margin, min_success
        self._hashes = skill_hashes or (lambda skills: {s: "" for s in skills})
        self._rows: dict[str, list[tuple[str, str, np.ndarray]]] = {}  # compartment -> [(route_id, state, vec)]

    def reload(self) -> None:
        self._rows = {}
        for rid, comp, state, vec in self.store.embeddings(("active", "shadow")):
            self._rows.setdefault(comp, []).append((rid, state, vec))

    # ------------------------------------------------------------------ matching (L0)
    def match(self, qvec: np.ndarray, compartment: str, caller_trust: str, text: str, now_ms: int, *, states: tuple[str, ...] = ("active",)) -> RouteMatch | None:
        rows = [r for r in self._rows.get(compartment, []) if r[1] in states]
        if not rows:
            return None
        sims = [float(r[2] @ qvec) for r in rows]
        order = sorted(range(len(rows)), key=lambda i: -sims[i])
        best = order[0]
        second = sims[order[1]] if len(order) > 1 else 0.0
        if sims[best] < self.tau or sims[best] - second < self.margin:
            return None
        rid = rows[best][0]
        route = self.store.get(rid)
        if route is None or route["state"] not in states:
            return None
        n_used = route["successes"] + route["failures"]
        if n_used and route["successes"] / n_used < self.min_success:
            return None
        if TRUST_RANK.get(caller_trust, 0) < TRUST_RANK.get(route["trust_floor"], 4):
            return None
        recipe = json.loads(route["recipe_json"])
        skills = sorted({n["skill"] for n in recipe["nodes"]})
        if recipe_hash(recipe, self._hashes(skills)) != route["tree_hash"]:
            self.demote(rid, reason="skill_hash_changed")
            return None
        nodes = self.instantiate(recipe, text, now_ms)
        if nodes is None:
            return None
        return RouteMatch(rid, sims[best], sims[best] - second, route["state"], nodes, [n["skill"] for n in recipe["nodes"]])

    @staticmethod
    def instantiate(recipe: dict[str, Any], text: str, now_ms: int) -> list[dict[str, Any]] | None:
        nodes = []
        for n in recipe["nodes"]:
            params, missing = instantiate_value(n["params"], text, now_ms)
            if missing:
                return None  # a slot cannot be filled from this request: fall through to the next rung
            nodes.append({**n, "params": params})
        return nodes

    # ------------------------------------------------------------------ learning (P1 to P7)
    async def propose(self, *, embed: Callable[[str], Awaitable[np.ndarray]], request_text: str, compartment: str, nodes: list[dict[str, Any]], task_id: str | None,
                      task_state: str, plan_source: str, plan_edited: bool, ancestry_min_trust: str, all_criteria_deterministic: bool, now_ms: int,
                      intent_family: str = "general", user_saved_as_routine: bool = False) -> CandidateResult:
        def reject(gate: str) -> CandidateResult:
            self.store.add_event(None, "candidate_rejected", tier=gate, ok=False)
            return CandidateResult(False, None, gate)

        if task_state != "succeeded":
            return reject("P1")
        if not all_criteria_deterministic:
            return reject("P2")
        if plan_source not in ("L2", "L3") or plan_edited:
            return reject("P3")
        if ancestry_min_trust in ("received", "web") and not user_saved_as_routine:
            return reject("P4")
        found = detect(request_text, now_ms)
        sig, recipe_nodes, risk = signature(request_text, found), [], 0
        for n in nodes:
            params = templatize(n["params"], found)
            for v in (x for x in params.values() if isinstance(x, str)):
                if "{slot:" not in v and "{ref:" not in v and any(p in v for p in _PERSONAL):
                    return reject("P5")  # literal personal data would be baked into the recipe
            recipe_nodes.append({"id": n["id"], "skill": n["skill"], "params": params, "depends_on": n.get("depends_on", []), "criteria": n.get("criteria", [])})
            risk = max(risk, int(n.get("tier", 0)))
        skills = sorted({n["skill"] for n in recipe_nodes})
        hashes = self._hashes(skills)
        if any(not hashes.get(s, "x") for s in skills) or len(hashes) < len(skills):
            return reject("P7")
        recipe = {"nodes": recipe_nodes, "example_request": request_text}
        qvec = await embed(sig)
        for rid, _state, vec in self._rows.get(compartment, []):
            if float(vec @ qvec) >= DUP_SIMILARITY:
                return CandidateResult(False, rid, "DUP")
        rid = self.store.insert(compartment=compartment, signature_text=sig, intent_family=intent_family, recipe=recipe, risk_tier_hint=risk,
                                trust_floor=ancestry_min_trust, tree_hash=recipe_hash(recipe, hashes), created_from_task=task_id, state="shadow")
        self.store.put_embedding(rid, "route", qvec)
        self.store.add_event(rid, "candidate_created", tier="shadow")
        self.reload()
        return CandidateResult(True, rid)

    # ------------------------------------------------------------------ shadow, outcomes, state changes
    def record_shadow(self, route_id: str, agreed: bool) -> str:
        route = self.store.get(route_id)
        if route is None or route["state"] != "shadow":
            return "ignored"
        if not agreed:
            self.store.update(route_id, shadow_agree=0)
            self.store.add_event(route_id, "shadow_disagree", ok=False)
            return "shadow"
        n = route["shadow_agree"] + 1
        self.store.update(route_id, shadow_agree=n)
        self.store.add_event(route_id, "shadow_agree", ok=True)
        if n >= SHADOW_NEEDED:
            self.promote(route_id)
            return "active"
        return "shadow"

    def user_thumbs_up(self, route_id: str) -> str:
        return self.record_shadow(route_id, True)

    def promote(self, route_id: str) -> None:
        route = self.store.get(route_id)
        if route is None:
            return
        self.store.update(route_id, state="active")
        self.store.add_event(route_id, "promoted", tier="L0")
        recipe = json.loads(route["recipe_json"])
        first = recipe["nodes"][0]["skill"] if recipe["nodes"] else None
        self.store.add_example(recipe.get("example_request", route["signature_text"]), {"family": route["intent_family"], "skill": first}, family=route["intent_family"],
                               skill=first, plan_needed=len(recipe["nodes"]) > 1, risk=str(route["risk_tier_hint"]), source="l3_verified", trust=route["trust_floor"])
        self.reload()

    def demote(self, route_id: str, *, reason: str = "failures") -> None:
        route = self.store.get(route_id)
        if route is None:
            return
        n = route["demotions"] + 1
        self.store.update(route_id, state="retired" if n >= MAX_DEMOTIONS else "demoted", demotions=n, shadow_agree=0)
        self.store.add_event(route_id, "demoted", tier=reason, ok=False)
        self.reload()

    def reverify(self, route_id: str) -> None:
        route = self.store.get(route_id)
        if route and route["state"] == "demoted":
            self.store.update(route_id, state="shadow", shadow_agree=0)
            self.reload()

    def record_outcome(self, route_id: str, ok: bool, now_ms: int) -> None:
        route = self.store.get(route_id)
        if route is None:
            return
        if ok:
            self.store.update(route_id, successes=route["successes"] + 1, last_used=now_ms)
            self.store.add_event(route_id, "outcome", ok=True)
            return
        failures = route["failures"] + 1
        self.store.update(route_id, failures=failures, last_used=now_ms)
        self.store.add_event(route_id, "outcome", ok=False)
        recent = [e for e in self.store.events(kind="outcome", route_id=route_id)][-2:]
        rate = route["successes"] / max(1, route["successes"] + failures)
        if rate < self.min_success or (len(recent) == 2 and not any(e["ok"] for e in recent)):
            self.demote(route_id)

    def delete(self, route_id: str) -> None:
        self.store.delete(route_id)
        self.reload()
