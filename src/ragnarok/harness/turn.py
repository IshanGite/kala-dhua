"""One turn of the agent (ARCHITECTURE 6.1): receive, retrieve, route, plan, authorize + execute, verify, record, LEARN.

The learning step is what makes the cascade self-improving: a fully successful, untainted, code-verified L2/L3 task proposes a route;
shadow routes are compared against the plan the big path just produced; active routes (L0) report their outcome so a failing route is demoted.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import jsonschema
import numpy as np

from ragnarok.core.clock import get_clock
from ragnarok.core.errors import ModelError
from ragnarok.harness.answer import (
    CAPABILITIES,
    CAPABILITY_ASK,
    COMPLAINT,
    TIME_ASK,
    Answered,
    Answerer,
    is_agenda,
    is_catchup,
    is_smalltalk,
    source_recall,
)
from ragnarok.harness.calc import try_calc
from ragnarok.harness.decompose import decompose, is_compound
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
from ragnarok.router.intent_rules import match_intent
from ragnarok.router.route_library import RouteLibrary
from ragnarok.router.slots import detect
from ragnarok.router.types import RouteDecision
from ragnarok.skills.manifest import Manifest
from ragnarok.skills.registry import SkillRegistry
from ragnarok.store.vectors import VectorIndex

FREE = "{free}"  # a free-text parameter in a stored route: refilled from the new request, never replayed from the old one
CRITERIA: dict[str, list[dict]] = {
    "reminder.add": [{"kind": "field_present", "path": "reminder_id"}], "reminder.list": [{"kind": "ok"}],
    "mail.draft": [{"kind": "field_present", "path": "draft"}], "mail.send": [{"kind": "equals", "path": "sent", "value": True}],
    "chat.reply": [{"kind": "equals", "path": "posted", "value": True}], "calendar.add": [{"kind": "equals", "path": "added", "value": True}], "mail.reply": [{"kind": "equals", "path": "sent", "value": True}], "drive.create": [{"kind": "equals", "path": "created", "value": True}], "memory.search": [{"kind": "ok"}], "calendar.agenda": [{"kind": "ok"}],
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
    timings: dict[str, int] = field(default_factory=dict)  # milliseconds per stage: where a turn's time went


class Agent:
    def __init__(self, *, mem: MemoryService, ledger: Ledger, reader: MemoryReader, index: VectorIndex, gateway: Gateway, cascade: Cascade, planner: Planner,
                 executor: Executor, registry: SkillRegistry, library: RouteLibrary | None, routes: RoutesStore | None, ctx: ExecContext, answerer: Answerer | None = None, fast_refuse: bool = False, calendar_mirror: Any = None, memcmd: Any = None) -> None:
        self.memcmd = memcmd
        self._mirror = calendar_mirror  # callable -> bool: is Google write access connected, so reminders also go on the calendar
        self.answerer = answerer
        self.fast_refuse = fast_refuse  # the daemon sets it: one unresolved action is refused at once instead of spending the slow planner on it
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

    ACTION_WORDS = {
        "chat.reply": r"\b(tell|message|text|reply|respond|ping|post|dm|send)\b", "mail.send": r"\b(send|email|mail|reply|forward|write back)\b",
        "mail.draft": r"\b(draft|write|compose|prepare|reply|email)\b", "calendar.add": r"\b(calendar|schedule|book|block|meeting|appointment|put|add)\b", "mail.reply": r"\b(reply|respond|answer|write back)\b", "python.run": r"\b(run|execute|python|script|code|program)\b", "drive.create": r"\b(create|write|save|make|draft)\b.*\b(doc|document|drive|note|notes|file)\b",
        "reminder.add": r"\b(remind|reminder|to-?do|note|remember)\b",
    }
    QUESTIONLIKE = re.compile(r"^\s*(what|when|where|who|whom|whose|which|why|how|is|are|was|were|do|does|did|can|could|will|would|should|any|has|have)\b|\?\s*$", re.I)
    HISTORYLIKE = re.compile(r"\b(i just (asked|said|told)|you (just )?(said|told|answered)|earlier (we|you|i)|previous (question|message|answer)|what did i (just )?ask|what have i asked|(earlier|before|previously|so far)\b.*\b(chat|conversation)|(this|our) (chat|conversation))\b", re.I)

    _clarify: str | None = None
    _pending: tuple[str, float] | None = None  # (the half-finished request, when it was asked)
    ACTIONISH = re.compile(r"\b(send|post|message|tell|text|email|mail|remind|schedule|create|add|put|reply|respond|forward|delete|remove|book|cancel|save|write|draft|share|invite|set up)\b", re.I)
    POLITE = re.compile(r"^\s*(?:please\s+)?(?:can|could|would|will) you\b", re.I)
    PRONOUN = re.compile(r"\b(it|that|this|they|them|those|these|he|she|him|her|there|then)\b", re.I)
    NO_ACTION = ("I did not do anything. That sounds like a request to act, but I could not tell which app, which person or what to say. "
                 "Try: 'tell Ravi on telegram that I will be late', 'send an email to name@example.com saying ...', 'remind me to call the bank tomorrow at 5pm', or 'put a call with Priya on my calendar tomorrow at 4pm'.")

    @classmethod
    def _multistep(cls, request: str) -> bool:
        return len({m.group(0).lower() for m in cls.ACTIONISH.finditer(request)}) >= 2 or bool(re.search(r"\bthen\b|\band then\b|\bafter that\b", request, re.I))

    def _is_question(self, request: str) -> bool:
        return bool(self.QUESTIONLIKE.search(request)) and not self.POLITE.match(request)

    def _is_action_request(self, request: str) -> bool:
        return bool(self.ACTIONISH.search(request)) and not self._is_question(request)

    @staticmethod
    def _hint(request: str, history: list[tuple[str, str]] | None) -> str:
        """A short follow-up ('who sent it', 'and when?') is searched together with the previous question so 'it' means something."""
        if not history or len(request.split()) > 8 or not Agent.PRONOUN.search(request) and not request.lower().startswith(("and ", "who", "when", "where")):
            return ""
        last = next((c for r, c in reversed(history) if r == "user"), "")
        return last[:200]

    async def _say(self, request: str, *, history: list[tuple[str, str]] | None, general: bool) -> Answered:
        """Prose answers only. A request that sounds like an action never gets one: prose must not look like something was done."""
        assert self.answerer is not None
        if self._is_action_request(request):
            return Answered(self.NO_ACTION, notes=["no_action_taken"])
        ans = await self.answerer.answer(request, general=False, history=history, context_hint=self._hint(request, history))
        if not ans.grounded and general:
            ans = await self.answerer.answer(request, general=True, history=history)
        return ans

    def _wants_any_write(self, request: str) -> bool:
        return any(re.search(p, request, re.I) for sk, p in self.ACTION_WORDS.items() if sk != "reminder.add") and not self._is_question(request)

    def _wants_action(self, skill: str, request: str) -> bool:
        """A write-capable skill runs only if the words of the request actually ask for it: small talk and questions never trigger one."""
        m = self._manifest(skill)
        if m is None or (m.tier < 1 and not m.external_write):
            return True
        pat = self.ACTION_WORDS.get(skill)
        return bool(pat and re.search(pat, request, re.I)) and not (self._is_question(request) and skill in ("chat.reply", "mail.send", "mail.reply", "calendar.add", "drive.create"))

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
            nodes = [PlanNode(id="n1", goal=text[:200], skill=skill, params={"text": what, "when": when}, criteria=CRITERIA[skill])]
            if when and self._mirror is not None and self._mirror() and self._manifest("calendar.add") is not None:
                nodes.append(PlanNode(id="n2", goal="put it on the calendar", skill="calendar.add", params={"title": what, "when": when}, criteria=CRITERIA["calendar.add"]))  # needs its own click
            return Plan(nodes=nodes)
        if skill == "calendar.add":
            from ragnarok.router.when import parse_when, split_event

            title, when = split_event(text)
            from datetime import datetime as _dt

            if when and parse_when(when, _dt.fromtimestamp(get_clock().now_ms() / 1000)) is not None:
                return Plan(nodes=[PlanNode(id="n1", goal=text[:200], skill=skill, params={"title": title, "when": when}, criteria=CRITERIA[skill])])
        if skill in ("mail.send", "mail.draft"):
            from ragnarok.router.when import split_mail

            to, subject, body = split_mail(text)
            if not to:
                self._clarify = "Which email address should it go to? For example: send an email to name@example.com saying ..."
                return None
            if not body and not subject:
                self._clarify = "What should the email say? For example: send an email to name@example.com requesting leave for tomorrow."
                return None
            if not body:  # the user gave an intent, not the words: the small writer composes it, and the approval card shows the full text before anything is sent
                subject, body = await self._write_mail(to, subject)
                if not body:
                    self._clarify = "I could not compose that. Tell me what it should say: send an email to name@example.com saying ..."
                    return None
            subject = subject or (body[:50].rstrip(".") if body else "Message")
            return Plan(nodes=[PlanNode(id="n1", goal=text[:200], skill=skill, params={"to": to, "subject": subject, "body": body}, criteria=CRITERIA[skill])])
        if skill == "drive.create":
            from ragnarok.router.when import split_doc

            title, doc = split_doc(text)
            if not title or not doc:
                self._clarify = "What should the document be called and what should it say? For example: create a document in Drive called Plan saying the key dates."
                return None
            return Plan(nodes=[PlanNode(id="n1", goal=text[:200], skill=skill, params={"title": title, "text": doc}, criteria=CRITERIA[skill])])
        if skill == "mail.reply":
            from ragnarok.router.when import split_reply

            target, body = split_reply(text)
            if target and body:
                return Plan(nodes=[PlanNode(id="n1", goal=text[:200], skill=skill, params={"target": target, "body": body}, criteria=CRITERIA[skill])])
            self._clarify = "What should the reply say? Tell me like: reply to Dev saying I will send it Thursday."
            return None
        if skill == "chat.reply":
            from ragnarok.router.when import split_chat

            platform, target, body = split_chat(text)
            if platform and body and not target:  # nobody named: the most recent conversation on that app, shown on the approval card so it can be corrected
                recent = self.reader.recent_items(limit=1, source=platform)
                target = str(recent[0]["title"]) if recent and recent[0]["title"] else ""
                if not target:
                    self._clarify = f"I have not read anything on {platform} yet, so I do not know who to message. Say who: 'tell Ravi on {platform} that ...'."
                    return None
            if platform and target and body:
                return Plan(nodes=[PlanNode(id="n1", goal=text[:200], skill=skill, params={"platform": platform, "target": target, "text": body}, criteria=CRITERIA[skill])])
            self._clarify = "Who should I message, on which app, and what should it say? For example: tell Ravi on telegram that I will be late."
            return None
        if m.tier >= 1 or m.external_write:
            snippets = []  # what leaves the machine is built from the user's own words only, never from memory or received text
        deadline = get_clock().monotonic() + 8
        for _ in range(2):
            res = await self.gw.chat(role="extractor", schema=m.parameters, max_tokens=300, deadline=deadline, priority=Priority.INTERACTIVE, seed=0, temperature=0.0,
                                     messages=[{"role": "system", "content": f"Fill the parameters for the skill '{skill}'. Use only facts from the request and context; do not add anything the user did not say. {('Current local time: ' + self._now_text() + '; write times as ISO 8601 local date-time. ') if 'when' in m.parameters.get('properties', {}) else ''}JSON only."},
                                               {"role": "user", "content": f"Context:\n{chr(10).join(snippets[:4])}\n\nRequest: {text}"}])
            try:
                jsonschema.validate(res.parsed or {}, m.parameters)
            except jsonschema.ValidationError:
                continue
            return Plan(nodes=[PlanNode(id="n1", goal=text[:200], skill=skill, params=res.parsed or {}, criteria=CRITERIA.get(skill) or [{"kind": "ok"}])])
        return None

    async def _write_mail(self, to: str, topic: str) -> tuple[str, str]:
        """Subject and body for a DRAFT from a topic, by the small fast writer model (no reasoning); the user reviews before anything is sent."""
        schema = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}
        try:
            res = await self.gw.chat(role="writer", schema=schema, max_tokens=110, deadline=get_clock().monotonic() + 10, priority=Priority.INTERACTIVE, seed=0, temperature=0.0,
                                     messages=[{"role": "system", "content": "Write a short, polite email for the sender. Two or three sentences. Use only the facts given; invent no dates, amounts or names. Do not use placeholders like [Name]; end with just a thanks. JSON {subject, body}."},
                                               {"role": "user", "content": f"To: {to}\nAbout: {topic[:300]}"}])
        except Exception:  # noqa: BLE001
            return "", ""
        d = res.parsed or {}
        return str(d.get("subject", ""))[:150], str(d.get("body", ""))[:1500]

    USER_FIXABLE = {"E_APPROVAL_DENIED", "E_APPROVAL_INVALID", "E_NOT_CONNECTED", "E_UNKNOWN_TARGET", "E_BAD_RECIPIENT", "E_NO_TIME", "E_AUTH", "E_DENIED", "E_PANIC", "E_SANDBOX_UNAVAILABLE"}

    async def _reflect(self, request: str, decision: RouteDecision, plan: Plan, outcome: TaskOutcome, snippets: list[str]) -> TaskOutcome:
        """FR3.3: when a read-only or local step fails or does not meet its criteria, look at what went wrong and try ONE adjusted plan. Anything that
        sends, posts or writes outside is never re-planned by the machine, and anything the user must fix (a denied card, a missing permission) is not retried."""
        if outcome.state == "succeeded" or not outcome.errors:
            return outcome
        if any(code in self.USER_FIXABLE for code in outcome.errors.values()):
            return outcome
        by_id = {n.id: n for n in plan.nodes}
        for nid in outcome.errors:
            m = self._manifest(by_id[nid].skill) if nid in by_id else None
            if m is None or m.external_write or m.side_effects == "irreversible":
                return outcome
        failed = "; ".join(f"step {nid} ({by_id[nid].skill}) failed with {code}" for nid, code in outcome.errors.items() if nid in by_id)
        manifests = [m for n in decision.shortlist if (m := self._manifest(n)) is not None] or self._all_manifests()[:5]
        pr = await self.planner.plan(f"{request}\n[Self-correction: the previous attempt did not work: {failed}. Choose a different approach or different parameters.]", manifests, snippets=snippets,
                                     deadline=get_clock().monotonic() + 30)
        if pr.plan is None or [(n.skill, n.params) for n in pr.plan.nodes] == [(n.skill, n.params) for n in plan.nodes]:
            decision.trace.append(f"Self-correction: {failed}; I could not find a different approach")
            return outcome
        decision.trace.append(f"Self-correction: {failed}; trying again with {', '.join(n.skill for n in pr.plan.nodes)}")
        tid = self.executor.start_task(request, pr.plan, self.ctx, plan_meta={"tier": decision.tier, "retry_of": outcome.task_id})
        return await self.executor.run_task(tid, self.ctx)

    async def _route_plan(self, d: RouteDecision, request: str) -> Plan | None:
        nodes = d.route_nodes or []
        if any(FREE in str(n.get("params")) for n in nodes):
            if {n["skill"] for n in nodes} == {"reminder.add", "calendar.add"}:  # the reminder-on-calendar pair is refilled by code from the new request
                from ragnarok.router.when import split_reminder

                what, when = split_reminder(request)
                nodes = [{**n, "params": ({"text": what, "when": when} if n["skill"] == "reminder.add" else {"title": what, "when": when})} for n in nodes]
            elif len(nodes) != 1:
                return None  # a multi-step route with free text is re-planned, not replayed
            else:
                nodes = None  # type: ignore[assignment]
        if nodes is None:
            nodes = d.route_nodes or []
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

    async def handle(self, request: str, *, trust: str = "user_direct", history: list[tuple[str, str]] | None = None) -> TurnResult:
        t0 = time.perf_counter()
        if self._pending is not None:  # the last turn asked for a missing detail: a short plain reply completes that request
            prev, asked = self._pending
            self._pending = None
            if time.monotonic() - asked < 240 and len(request.split()) <= 14 and match_intent(request) is None and not self._is_question(request) and not self._is_action_request(request):
                request = f"{prev} {request}"
        tm: dict[str, int] = {}
        mark = lambda name, since: tm.__setitem__(name, round((time.perf_counter() - since) * 1000))  # noqa: E731
        now = get_clock().now_ms()
        eid = self.mem.write_event(EventIn(compartment=self.ctx.compartment, kind="user_message", actor="user", content=request, trust=trust))  # type: ignore[arg-type]
        mark("log", t0)
        if self.memcmd is not None and trust == "user_direct":  # remembering and recalling what the user told us, and our shared history: all by code, all local
            said = self.memcmd.remember(request, eid) or self.memcmd.recall(request) or self.memcmd.history(request)
            if said:
                dec = RouteDecision(tier="L1", intent_family="memory_cmd", skills=[], plan_needed=False, risk_floor=0, memory_types=["semantic", "episodic"], confidence=1.0, latency_ms=0.0, backend="rule")
                return TurnResult("L1", dec, None, None, (time.perf_counter() - t0) * 1000, answer=Answered(said, grounded=True), timings=tm)
        decomposer_note = ""
        if trust in ("user_direct", "agent") and is_compound(request):  # FR3.1: several steps in one request become a small execution graph, by code
            d = decompose(request)
            if d.plan is not None and validate_plan(d.plan, self._manifest).ok:
                dec = RouteDecision(tier="L1", intent_family="compound", skills=[n.skill for n in d.plan.nodes], plan_needed=True, risk_floor=0, memory_types=memory_types_hint(request),
                                    confidence=1.0, latency_ms=0.0, backend="decomposer",
                                    trace=[f"Split into {len(d.clauses)} steps by code: " + " then ".join(f"{n.skill}" for n in d.plan.nodes) + "; later steps use earlier results, so anything derived from outside content asks for your click"])
                return await self._run_plan(request, dec, d.plan, "C", [], t0, tm)
            decomposer_note = f"The step splitter could not handle it ({d.reason}), so the planner model tries"  # falls through to the cascade and the LLM planner
        if self.answerer is not None and is_catchup(request):  # a briefing is read-only and needs no routing
            ans = await self.answerer.catch_up(request)
            dec = RouteDecision(tier="L1", intent_family="catchup", skills=[], plan_needed=False, risk_floor=0, memory_types=[], confidence=1.0, latency_ms=0.0, backend="catchup")
            return TurnResult("L1", dec, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        if self.answerer is not None and (srcs := source_recall(request)) is not None and not is_agenda(request) and not is_catchup(request):
            ans = await self.answerer.recent_from(srcs)
            dec = RouteDecision(tier="L1", intent_family="recall", skills=[], plan_needed=False, risk_floor=0, memory_types=["episodic"], confidence=1.0, latency_ms=0.0, backend="rule")
            return TurnResult("L1", dec, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        if (calc := try_calc(request)) is not None:  # arithmetic is done by code, never by the model
            dec = RouteDecision(tier="L1", intent_family="calc", skills=[], plan_needed=False, risk_floor=0, memory_types=[], confidence=1.0, latency_ms=0.0, backend="rule", trace=["Arithmetic evaluated by code (a model is not trusted with sums)"])
            return TurnResult("L1", dec, None, None, (time.perf_counter() - t0) * 1000, answer=Answered(calc if " " in calc else f"= {calc}", grounded=True), timings=tm)
        if self.answerer is not None and TIME_ASK.search(request) and not self._is_action_request(request):
            from datetime import datetime as _dt

            n = _dt.fromtimestamp(get_clock().now_ms() / 1000).astimezone()
            txt = n.strftime("It is %H:%M on %A %d %B %Y") + f" ({n.tzname()})."
            dec = RouteDecision(tier="L1", intent_family="clock", skills=[], plan_needed=False, risk_floor=0, memory_types=[], confidence=1.0, latency_ms=0.0, backend="rule")
            return TurnResult("L1", dec, None, None, (time.perf_counter() - t0) * 1000, answer=Answered(txt), timings=tm)
        if self.answerer is not None and CAPABILITY_ASK.search(request) and not self._is_action_request(request.replace("can you", "")):
            dec = RouteDecision(tier="L1", intent_family="capabilities", skills=[], plan_needed=False, risk_floor=0, memory_types=[], confidence=1.0, latency_ms=0.0, backend="rule")
            return TurnResult("L1", dec, None, None, (time.perf_counter() - t0) * 1000, answer=Answered(CAPABILITIES))
        if self.answerer is not None and COMPLAINT.search(request) and history:
            dec = RouteDecision(tier="L1", intent_family="complaint", skills=[], plan_needed=False, risk_floor=0, memory_types=[], confidence=1.0, latency_ms=0.0, backend="rule")
            msg = ("Sorry about that. Tell me again as a clear command and I will do it; anything that changes something outside this computer waits for your click in the Approvals tab, "
                   "so if no card appears there, I did not understand it as an action.")
            return TurnResult("L1", dec, None, None, (time.perf_counter() - t0) * 1000, answer=Answered(msg))
        if self.answerer is not None and (is_smalltalk(request) or is_agenda(request)):  # conversation and agendas need no routing and no tool
            ans = await (self.answerer._general(request, history) if is_smalltalk(request) else self.answerer.agenda(request))
            dec = RouteDecision(tier="L1", intent_family="general" if is_smalltalk(request) else "agenda", skills=[], plan_needed=False, risk_floor=0, memory_types=[], confidence=1.0, latency_ms=0.0, backend="rule")
            return TurnResult("L1", dec, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        if self.answerer is not None and self.fast_refuse and self._is_question(request) and not self._is_action_request(request) and match_intent(request) is None:
            t_ans = time.perf_counter()  # a plain question needs no classification: retrieve and answer
            ans = await self._say(request, history=history, general=True)
            mark("answer", t_ans)
            dec = RouteDecision(tier="L1", intent_family="question", skills=[], plan_needed=False, risk_floor=0, memory_types=memory_types_hint(request), confidence=1.0, latency_ms=0.0, backend="rule")
            return TurnResult("L1", dec, None, None, (time.perf_counter() - t0) * 1000, answer=ans, timings=tm)
        t_route = time.perf_counter()
        decision = await self.cascade.route(request, compartment=self.ctx.compartment, caller_trust=trust, now_ms=now)
        mark("route", t_route)
        decision.memory_types = memory_types_hint(request)
        if decomposer_note:
            decision.trace.append(decomposer_note)
        if decision.intent_family == "unavailable" and self.answerer is not None:  # degrade: keyword search over memory, plain honesty otherwise
            ans = await self._say(request, history=history, general=False) if not self._is_action_request(request) else Answered("The model server is not running, so I cannot work out that request right now. Reminders, agenda and time questions still work.")
            return TurnResult("L3", decision, None, None, (time.perf_counter() - t0) * 1000, answer=ans, timings=tm)
        if decision.intent_family in ("memory", "general") and self.answerer is not None and decision.tier != "L0":
            ans = await self._say(request, history=history, general=decision.intent_family == "general")
            return TurnResult(decision.tier, decision, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        if self.answerer is not None and history is not None and self.HISTORYLIKE.search(request):
            ans = await self.answerer._general(request, history)
            return TurnResult(decision.tier, decision, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        if self.answerer is not None and decision.tier != "L0" and decision.skills and not all(self._wants_action(sk, request) for sk in decision.skills[:1]):
            decision.notes.append("no_action_asked_so_no_tool")
            ans = await self._say(request, history=history, general=True)
            return TurnResult(decision.tier, decision, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        if self.answerer is not None and decision.tier == "L3" and not self._is_action_request(request):  # not an action: answer from memory, do not hand it to the slow planner
            ans = await self._say(request, history=history, general=True)
            return TurnResult(decision.tier, decision, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        snippets = await self._snippets(request)
        plan: Plan | None = None
        plan_source = decision.tier if decision.tier != "L0" else "L0"
        if decision.tier == "L1" and decision.backend in ("rule", "embedding", "laya"):
            plan_source = "L2"  # a fast rung chose it; the deterministic success check and two shadow agreements are what earn a route (gates P1-P7 still apply)
        clarification = None
        if decision.tier == "L0":
            plan = await self._route_plan(decision, request)
            if plan is None:  # the learned route no longer fits: fall back, never guess
                decision.notes.append("route_invalid_fell_back")
                decision.tier, plan_source = "L3", "L3"
        if plan is None and decision.tier in ("L1", "L2"):
            plan = await self._single_skill_plan(request, decision.skills[0], snippets)
            if plan is None and getattr(self, "_clarify", None):
                msg, self._clarify = self._clarify, None
                self._pending = (request, time.monotonic())
                return TurnResult(decision.tier, decision, None, msg, (time.perf_counter() - t0) * 1000)
            if plan is None:
                decision.tier, plan_source = "L3", "L3"
        if plan is None and self.fast_refuse and self._is_action_request(request) and not self._multistep(request):  # one unresolved action: say so now, do not burn 30 s of planner on it
            return TurnResult(decision.tier, decision, None, self.NO_ACTION, (time.perf_counter() - t0) * 1000)
        if plan is None:
            manifests = [m for n in decision.shortlist if (m := self._manifest(n)) is not None] or self._all_manifests()[:5]
            pr = await self.planner.plan(request, manifests, snippets=snippets, deadline=get_clock().monotonic() + 30)
            plan, clarification = pr.plan, pr.clarification
        if plan is None and self.answerer is not None and self._is_action_request(request) and not decision.notes.count("clarified"):
            decision.notes.append("action_not_understood")
            return TurnResult(decision.tier, decision, None, self.NO_ACTION, (time.perf_counter() - t0) * 1000)
        if plan is None and self.answerer is not None and not self._wants_any_write(request):
            ans = await self.answerer.answer(request, general=False, history=history, context_hint=self._hint(request, history))  # nothing safe to do: try to just answer from memory
            if ans.grounded:
                return TurnResult(decision.tier, decision, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        if plan is None:
            self.mem.write_event(EventIn(compartment=self.ctx.compartment, kind="clarification", actor="agent", content=clarification or "no plan", trust="agent"))
            return TurnResult(decision.tier, decision, None, clarification, (time.perf_counter() - t0) * 1000)

        if self.answerer is not None and [n.skill for n in plan.nodes] == ["memory.search"]:  # a lookup is answered in words, with citations
            ans = await self.answerer.answer(request)
            return TurnResult(decision.tier, decision, None, None, (time.perf_counter() - t0) * 1000, answer=ans)
        return await self._run_plan(request, decision, plan, plan_source, snippets, t0, tm)

    async def _run_plan(self, request: str, decision: RouteDecision, plan: Plan, plan_source: str, snippets: list[str], t0: float, tm: dict[str, int]) -> TurnResult:
        mark = lambda name, since: tm.__setitem__(name, round((time.perf_counter() - since) * 1000))  # noqa: E731
        mark("plan", t0 + (tm.get("log", 0) + tm.get("route", 0)) / 1000)
        t_exec = time.perf_counter()
        tid = self.executor.start_task(request, plan, self.ctx, plan_meta={"tier": decision.tier, "route_id": decision.route_id})
        outcome = await self.executor.run_task(tid, self.ctx)
        mark("run", t_exec)
        for n in plan.nodes:
            m = self._manifest(n.skill)
            if m is not None:
                shown = ", ".join(f"{k}={str(v)[:50]}" for k, v in n.params.items())
                decision.trace.append(f"Step {n.id}: {n.skill} (risk tier {int(m.tier)}{', needs your click' if m.external_write or int(m.tier) >= 2 else ''}) with {shown}")
        decision.trace.append("Result: " + ", ".join(f"{k}={v}" for k, v in outcome.node_states.items()))
        outcome = await self._reflect(request, decision, plan, outcome, snippets)
        t_learn = time.perf_counter()
        try:
            learned = await self._learn(request, decision, plan, plan_source, outcome)
        except ModelError:  # learning is a bonus: with the embedding model down the task still succeeded
            learned = None
        mark("learn", t_learn)
        latency = (time.perf_counter() - t0) * 1000
        if self.routes is not None:
            self.routes.add_event(decision.route_id, "tier_used", tier=decision.tier, latency_ms=latency, ok=outcome.state == "succeeded", trace_id=tid[:12])
        self.ledger.record_decision("route", task_id=tid, tier=decision.tier, backend=decision.backend, choice={"skills": decision.skills, "plan_needed": decision.plan_needed},
                                    rationale="; ".join(decision.notes) or None, outcome={"state": outcome.state}, latency_ms=latency)
        self.mem.write_event(EventIn(compartment=self.ctx.compartment, kind="task_result", actor="agent", content=f"{request[:200]} -> {outcome.state}", trust="agent", task_id=tid))
        return TurnResult(decision.tier, decision, outcome, None, latency, learned, decision.notes, task_id=tid, timings=tm)

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
