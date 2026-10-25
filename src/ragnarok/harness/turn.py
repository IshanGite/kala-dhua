"""One turn of the agent (ARCHITECTURE 6.1): receive, retrieve, route, plan, authorize + execute, verify, record, LEARN.

The learning step is what makes the cascade self-improving: a fully successful, untainted, code-verified L2/L3 task proposes a route;
shadow routes are compared against the plan the big path just produced; active routes (L0) report their outcome so a failing route is demoted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import jsonschema
import numpy as np

from ragnarok.core.clock import get_clock
from ragnarok.harness.answer import Answered, Answerer
from ragnarok.harness.executor import ExecContext, Executor, TaskOutcome
from ragnarok.harness.plan import Plan, PlanNode, validate_plan
from ragnarok.harness.planner import Planner
from ragnarok.harness.verify import evaluate
from ragnarok.llm_gateway.client import Gateway, Priority
from ragnarok.memory.ledger import Ledger
from ragnarok.memory.models import EventIn
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.retrieval import retrieve
from ragnarok.memory.routes_store import RoutesStore
from ragnarok.memory.service import MemoryService
from ragnarok.router.cascade import Cascade, memory_types_hint
from ragnarok.router.route_library import RouteLibrary
from ragnarok.router.slots import detect
from ragnarok.router.types import RouteDecision
from ragnarok.skills.manifest import Manifest
from ragnarok.skills.registry import SkillRegistry
from ragnarok.store.vectors import VectorIndex

FREE = "{free}"  # a free-text parameter in a stored route: refilled from the new request, never replayed from the old one
CRITERIA: dict[str, list[dict]] = {
    "reminder.add": [{"kind": "field_present", "path": "reminder_id"}], "reminder.list": [{"kind": "field_present", "path": "reminders"}],
    "mail.draft": [{"kind": "field_present", "path": "draft"}], "mail.send": [{"kind": "equals", "path": "sent", "value": True}],
    "chat.reply": [{"kind": "equals", "path": "posted", "value": True}], "memory.search": [{"kind": "field_present", "path": "hits"}],
}


@dataclass
class TurnResult:
    tier: str
    decision: RouteDecision
    outcome: TaskOutcome | None
    clarification: str | None = None
    latency_ms: float = 0.0
    learned: str | None = None  # candidate_created | shadow_agree | promoted | none
    notes: list[str] = field(default_factory=list)
    answer: Answered | None = None
    task_id: str | None = None


class Agent:
    def __init__(self, *, mem: MemoryService, ledger: Ledger, reader: MemoryReader, index: VectorIndex, gateway: Gateway, cascade: Cascade, planner: Planner,
                 executor: Executor, registry: SkillRegistry, library: RouteLibrary | None, routes: RoutesStore | None, ctx: ExecContext, answerer: Answerer | None = None) -> None:
        self.answerer = answerer
        self.mem, self.ledger, self.reader, self.index, self.gw = mem, ledger, reader, index, gateway
        self.cascade, self.planner, self.executor, self.registry, self.library, self.routes, self.ctx = cascade, planner, executor, registry, library, routes, ctx

    def _manifest(self, name: str) -> Manifest | None:
        try:
            return self.registry.verify_runnable(name).manifest
        except Exception:  # noqa: BLE001 - an unrunnable skill simply does not exist for planning
            return None

    def _all_manifests(self) -> list[Manifest]:
        return [m for r in self.ledger.list_skills() if (m := self._manifest(r["skill_id"])) is not None]

    async def _snippets(self, text: str) -> list[str]:
        try:
            r = await retrieve(text, compartment=self.ctx.compartment, reader=self.reader, index=self.index, gateway=self.gw, now_ms=get_clock().now_ms(),
                               deadline=get_clock().monotonic() + 2, k=6, token_budget=600)
        except Exception:  # noqa: BLE001 - memory is helpful, never required
            return []
        return [f"[{h.kind} trust={h.trust}] {h.text[:300]}" for h in r.hits]

    @staticmethod
    def _now_text() -> str:
        from datetime import datetime

        return datetime.fromtimestamp(get_clock().now_ms() / 1000).astimezone().strftime("%A %Y-%m-%d %H:%M %Z")

    async def _single_skill_plan(self, text: str, skill: str, snippets: list[str]) -> Plan | None:
        m = self._manifest(skill)
        if m is None:
            return None
        if skill == "reminder.add":  # the words and the time come from the request by code; a 1.7B model is not trusted with either
            from ragnarok.router.when import split_reminder

            what, when = split_reminder(text)
            return Plan(nodes=[PlanNode(id="n1", goal=text[:200], skill=skill, params={"text": what, "when": when}, criteria=CRITERIA[skill])])
        if m.external_write:
            snippets = []  # what leaves the machine is built from the user's own words only, never from memory or received text
        deadline = get_clock().monotonic() + 8
        for _ in range(2):
            res = await self.gw.chat(role="extractor", schema=m.parameters, max_tokens=300, deadline=deadline, priority=Priority.INTERACTIVE, seed=0, temperature=0.0,
                                     messages=[{"role": "system", "content": f"Fill the parameters for the skill '{skill}'. Use only facts from the request and context; do not add anything the user did not say. Current local time: {self._now_text()}; write times as ISO 8601 local date-time. JSON only."},
                                               {"role": "user", "content": f"Context:\n{chr(10).join(snippets[:4])}\n\nRequest: {text}"}])
            try:
                jsonschema.validate(res.parsed or {}, m.parameters)
            except jsonschema.ValidationError:
                continue
            return Plan(nodes=[PlanNode(id="n1", goal=text[:200], skill=skill, params=res.parsed or {}, criteria=CRITERIA.get(skill) or [{"kind": "ok"}])])
        return None

    async def _route_plan(self, d: RouteDecision, request: str) -> Plan | None:
        nodes = d.route_nodes or []
        if any(FREE in str(n.get("params")) for n in nodes):
            if len(nodes) != 1:
                return None  # a multi-step route with free text is re-planned, not replayed
            fresh = await self._single_skill_plan(request, nodes[0]["skill"], [])
            if fresh is None:
                return None
            nodes = [{**nodes[0], "params": {k: (fresh.nodes[0].params.get(k, v) if v == FREE else v) for k, v in nodes[0]["params"].items()}}]
        try:
            plan = Plan(nodes=[PlanNode(id=n["id"], goal=n.get("goal", n["skill"]), skill=n["skill"], params=n["params"], depends_on=n.get("depends_on", []),
                                        criteria=n.get("criteria") or [{"kind": "ok"}]) for n in nodes])
        except Exception:  # noqa: BLE001
            return None
        return plan if validate_plan(plan, self._manifest).ok else None

    async def handle(self, request: str, *, trust: str = "user_direct") -> TurnResult:
        t0 = time.perf_counter()
        now = get_clock().now_ms()
        self.mem.write_event(EventIn(compartment=self.ctx.compartment, kind="user_message", actor="user", content=request, trust=trust))  # type: ignore[arg-type]
        decision = await self.cascade.route(request, compartment=self.ctx.compartment, caller_trust=trust, now_ms=now)
        decision.memory_types = memory_types_hint(request)
        if decision.intent_family in ("memory", "general") and self.answerer is not None and decision.tier != "L0":
            ans = await self.answerer.answer(request, general=decision.intent_family == "general")
            return TurnResult(decision.tier, decision, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        snippets = await self._snippets(request)
        plan: Plan | None = None
        plan_source = decision.tier if decision.tier != "L0" else "L0"
        clarification = None
        if decision.tier == "L0":
            plan = await self._route_plan(decision, request)
            if plan is None:  # the learned route no longer fits: fall back, never guess
                decision.notes.append("route_invalid_fell_back")
                decision.tier, plan_source = "L3", "L3"
        if plan is None and decision.tier in ("L1", "L2"):
            plan = await self._single_skill_plan(request, decision.skills[0], snippets)
            if plan is None:
                decision.tier, plan_source = "L3", "L3"
        if plan is None:
            manifests = [m for n in decision.shortlist if (m := self._manifest(n)) is not None] or self._all_manifests()[:5]
            pr = await self.planner.plan(request, manifests, snippets=snippets, deadline=get_clock().monotonic() + 30)
            plan, clarification = pr.plan, pr.clarification
        if plan is None:
            self.mem.write_event(EventIn(compartment=self.ctx.compartment, kind="clarification", actor="agent", content=clarification or "no plan", trust="agent"))
            return TurnResult(decision.tier, decision, None, clarification, (time.perf_counter() - t0) * 1000)

        if self.answerer is not None and [n.skill for n in plan.nodes] == ["memory.search"]:  # a lookup is answered in words, with citations
            ans = await self.answerer.answer(request)
            return TurnResult(decision.tier, decision, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        tid = self.executor.start_task(request, plan, self.ctx, plan_meta={"tier": decision.tier, "route_id": decision.route_id})
        outcome = await self.executor.run_task(tid, self.ctx)
        learned = await self._learn(request, decision, plan, plan_source, outcome)
        latency = (time.perf_counter() - t0) * 1000
        if self.routes is not None:
            self.routes.add_event(decision.route_id, "tier_used", tier=decision.tier, latency_ms=latency, ok=outcome.state == "succeeded", trace_id=tid[:12])
        self.ledger.record_decision("route", task_id=tid, tier=decision.tier, backend=decision.backend, choice={"skills": decision.skills, "plan_needed": decision.plan_needed},
                                    rationale="; ".join(decision.notes) or None, outcome={"state": outcome.state}, latency_ms=latency)
        self.mem.write_event(EventIn(compartment=self.ctx.compartment, kind="task_result", actor="agent", content=f"{request[:200]} -> {outcome.state}", trust="agent", task_id=tid))
        return TurnResult(decision.tier, decision, outcome, None, latency, learned, decision.notes, task_id=tid)

    def _free_text_marked(self, skill: str, params: dict, found: set[str]) -> dict:
        """Parameters a skill declares as free text (a reminder's words, a message body) are never baked into a stored route: they are marked for refill.
        Typed slots (times, emails) stay as slots; everything else is untouched."""
        m = self._manifest(skill)
        free = set(m.param_roles.get("free", [])) if m else set()
        return {k: (FREE if k in free and isinstance(v, str) and v.lower() not in found and "{ref:" not in v else v) for k, v in params.items()}

    async def _learn(self, request: str, d: RouteDecision, plan: Plan, source: str, outcome: TaskOutcome) -> str | None:
        if self.library is None:
            return None
        now = get_clock().now_ms()
        if d.tier == "L0" and d.route_id:
            self.library.record_outcome(d.route_id, outcome.state == "succeeded", now)
            return "route_used"
        learned = None
        if d.shadow_route_id and source in ("L2", "L3"):
            route = self.routes.get(d.shadow_route_id) if self.routes else None
            if route:
                import json

                chain = [n["skill"] for n in json.loads(route["recipe_json"])["nodes"]]
                state = self.library.record_shadow(d.shadow_route_id, chain == [n.skill for n in plan.nodes] and outcome.state == "succeeded")
                learned = "promoted" if state == "active" else "shadow_agree"
        if learned is None and source in ("L2", "L3"):
            ledger_nodes = {n["id"].split("-", 1)[1]: n for n in self.ledger.nodes_of(outcome.task_id)}
            det = all(all(c.deterministic for c in evaluate(n.criteria, outcome.results.get(n.id), workspace=self.ctx.workspace, readback=True)) for n in plan.nodes)
            found = {v.lower() for _, v, _ in detect(request, now)}
            nodes = [{"id": n.id, "skill": n.skill, "params": self._free_text_marked(n.skill, n.params, found), "depends_on": n.depends_on, "criteria": n.criteria,
                      "tier": ledger_nodes[n.id]["tier"]} for n in plan.nodes]

            async def embed(t: str) -> np.ndarray:
                return (await self.gw.embed([t], deadline=get_clock().monotonic() + 10, priority=Priority.BACKGROUND))[0]

            res = await self.library.propose(embed=embed, request_text=request, compartment=self.ctx.compartment, nodes=nodes, task_id=outcome.task_id,
                                             task_state=outcome.state, plan_source=source, plan_edited=False, ancestry_min_trust=outcome.min_trust,
                                             all_criteria_deterministic=det, now_ms=now)
            learned = "candidate_created" if res.created else None
        return learned
