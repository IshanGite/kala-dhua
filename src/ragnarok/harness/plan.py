"""Plan model and validator (FR3.1). The planner (a model) proposes; this pure code decides whether the plan may run.

Checks: acyclic, every skill registered and runnable, parameter references point to EARLIER nodes, every node has at least one
success criterion, node count within the cap, parameters match each skill's schema (references excluded), and high-risk nodes
are flagged so the user sees every approval the plan will need BEFORE it runs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from pydantic import BaseModel, Field

from ragnarok.skills.manifest import Manifest

MAX_NODES = 12
REF = re.compile(r"\{ref:([a-zA-Z0-9_-]+)(?:\.([a-zA-Z0-9_.\[\]-]+))?\}")


class PlanNode(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    goal: str = Field(max_length=300)
    skill: str
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    criteria: list[dict[str, Any]] = Field(default_factory=list)
    on_failure: str = Field(default="abort_branch", pattern="^(retry|skip|abort_branch)$")


class Plan(BaseModel):
    nodes: list[PlanNode]
    assumptions: list[str] = Field(default_factory=list)


PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "maxItems": MAX_NODES,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "goal": {"type": "string"},
                    "skill": {"type": "string"},
                    "params": {"type": "object"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "criteria": {"type": "array", "items": {"type": "object"}},
                    "on_failure": {"type": "string", "enum": ["retry", "skip", "abort_branch"]},
                },
                "required": ["id", "goal", "skill", "params", "depends_on", "criteria"],
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["nodes"],
}


@dataclass
class Validation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    approvals_needed: list[str] = field(default_factory=list)  # human-readable preview of every click this plan will need
    order: list[str] = field(default_factory=list)


def _refs_in(value: Any) -> list[str]:
    if isinstance(value, str):
        return [m.group(1) for m in REF.finditer(value)]
    if isinstance(value, dict):
        return [r for v in value.values() for r in _refs_in(v)]
    if isinstance(value, list):
        return [r for v in value for r in _refs_in(v)]
    return []


def _strip_refs(params: dict[str, Any]) -> dict[str, Any]:
    """Replace reference placeholders so the rest of the parameters can be schema-checked."""
    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return "x" if REF.fullmatch(v) else REF.sub("", v) or "x"
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(params)


def validate_plan(plan: Plan, manifest_of: Callable[[str], Manifest | None]) -> Validation:
    errs: list[str] = []
    ids = [n.id for n in plan.nodes]
    if not plan.nodes:
        errs.append("plan has no steps")
    if len(plan.nodes) > MAX_NODES:
        errs.append(f"plan has more than {MAX_NODES} steps")
    if len(set(ids)) != len(ids):
        errs.append("duplicate step ids")
    known = set(ids)
    approvals: list[str] = []
    for n in plan.nodes:
        m = manifest_of(n.skill)
        if m is None:
            errs.append(f"{n.id}: unknown skill {n.skill!r}")
            continue
        if not n.criteria:
            errs.append(f"{n.id}: no success criterion")
        for dep in n.depends_on:
            if dep not in known:
                errs.append(f"{n.id}: depends on unknown step {dep!r}")
        for ref in _refs_in(n.params):
            if ref not in known:
                errs.append(f"{n.id}: reference to unknown step {ref!r}")
            elif ref not in n.depends_on:
                errs.append(f"{n.id}: uses the result of {ref!r} without depending on it")
        try:
            jsonschema.validate(_strip_refs(n.params), m.parameters)
        except jsonschema.ValidationError as exc:
            errs.append(f"{n.id}: parameters do not match {n.skill}: {exc.message[:80]}")
        if int(m.tier) >= 2 or m.external_write:
            approvals.append(f"{n.id}: {n.skill} needs your approval (tier {int(m.tier)})")
    order = _toposort(plan, errs)
    return Validation(not errs, errs, approvals, order)


def _toposort(plan: Plan, errs: list[str]) -> list[str]:
    deps = {n.id: set(n.depends_on) & {x.id for x in plan.nodes} for n in plan.nodes}
    order: list[str] = []
    ready = sorted(i for i, d in deps.items() if not d)
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for i, d in deps.items():
            if cur in d:
                d.discard(cur)
                if not d and i not in order and i not in ready:
                    ready.append(i)
    if len(order) != len(plan.nodes):
        errs.append("the plan contains a cycle")
    return order
