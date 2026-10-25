"""NEW-R1: the self-improving cascade. The same task family, asked repeatedly, descends L3 -> ... -> L0 and gets faster; poisoned routes are never learned."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ragnarok.broker.approvals import Broker, UiSession
from ragnarok.core.clock import RealClock, SimClock, get_clock, set_clock
from ragnarok.harness.executor import ExecContext, Executor
from ragnarok.harness.planner import Planner
from ragnarok.harness.turn import Agent
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.memory.ledger import Ledger
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.routes_store import RoutesStore
from ragnarok.memory.service import MemoryService
from ragnarok.policy.engine import load_rules
from ragnarok.router.backends.fake import FakeBackend
from ragnarok.router.calibration import Calib, Calibrator
from ragnarok.router.cascade import Cascade
from ragnarok.router.route_library import RouteLibrary
from ragnarok.router.select import SkillSelector
from ragnarok.router.slots import extract_time
from ragnarok.security.guard import Guard
from ragnarok.skills.registry import SkillRegistry
from ragnarok.store.vectors import VectorIndex

HUMAN = UiSession(human=True, csrf_ok=True)
FAMILY = {"calendar": ["cal.read", "cal.update"], "mail": ["mail.draft"]}


def skill(root: Path, name: str, perms, props, *, side="none", dry=False, desc=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"name": name, "description": desc or f"Use when {name}.", "parameters": {"type": "object", "properties": props},
        "permissions": [{"permission": p} for p in perms], "side_effects": side, "dry_run": dry, "entrypoint": {"type": "native", "action": name}}), encoding="utf-8")
    return d


class SlowPlannerGateway(FakeGateway):
    """The fake 'big model': scripted plan, plus a delay standing in for the seconds a real 8B model takes."""

    def __init__(self, delay: float = 0.08) -> None:
        super().__init__()
        self.delay, self.planner_calls = delay, 0

    async def chat(self, **kw):
        if kw["role"] == "planner":
            self.planner_calls += 1
            await asyncio.sleep(self.delay)
            return self._outline(kw) if "steps" in kw["schema"]["properties"] else self._plan(kw)
        return await super().chat(**kw)

    def _outline(self, kw):
        from ragnarok.llm_gateway.client import ChatResult

        out = {"steps": [{"goal": "read calendar", "skill": "cal.read"}, {"goal": "move", "skill": "cal.update"}, {"goal": "tell", "skill": "mail.draft"}]}
        return ChatResult(json.dumps(out), out, "fake", 0, 0, 0.0, "planner")

    def _plan(self, kw):
        from ragnarok.llm_gateway.client import ChatResult

        text = kw["messages"][-1]["content"].split("Request:")[1].split("\n")[0]
        t = (extract_time(text) or ("09:00", ""))[0]
        plan = {"nodes": [
            {"id": "n1", "goal": "read the calendar", "skill": "cal.read", "params": {"range": "today"}, "depends_on": [], "criteria": [{"kind": "field_present", "path": "events"}]},
            {"id": "n2", "goal": "move the event", "skill": "cal.update", "params": {"event": "standup", "start": t}, "depends_on": ["n1"], "criteria": [{"kind": "field_present", "path": "updated"}]},
            {"id": "n3", "goal": "draft the note", "skill": "mail.draft", "params": {"body": f"Standup moved to {t}"}, "depends_on": ["n2"], "criteria": [{"kind": "field_present", "path": "draft"}]},
        ]}
        return ChatResult(json.dumps(plan), plan, "fake", 0, 0, 0.0, "planner")


@pytest.fixture
def world(tmp_path, keys):
    clock = SimClock(1_760_000_000_000)
    set_clock(clock)
    mem = MemoryService(tmp_path / "db" / "r.db", keys)
    ledger = Ledger(mem)
    broker = Broker(ledger, keys)
    routes = RoutesStore(mem)
    data, ws = tmp_path / "data", tmp_path / "ws"
    for d in ("run", "tmp", "skills"):
        (data / d).mkdir(parents=True)
    ws.mkdir()
    registry = SkillRegistry(ledger, broker, data / "skills")
    src = tmp_path / "src"
    for name, perms, props, kw in (
        ("cal.read", ["calendar.read"], {"range": {"type": "string"}}, dict(desc="Use when you need to look at the calendar for a day.")),
        ("cal.update", ["calendar.write"], {"event": {"type": "string"}, "start": {"type": "string"}}, dict(side="reversible", dry=True, desc="Use when an event must be moved to a new time.")),
        ("mail.draft", ["mail.draft"], {"body": {"type": "string"}}, dict(side="reversible", dry=True, desc="Use when a note to attendees must be drafted.")),
    ):
        registry.register(skill(src, name, perms, props, **kw), builtin=True)
    guard = Guard(registry, ledger, load_rules(), workspace_roots=[ws], run_dir=data / "run", budgets={"task": {"max_node_attempts": 500}, "hourly": {}, "mail": {"max_recipients": 5}})
    calls: list[str] = []

    async def cal_read(p, ctx, *, dry_run):
        return {"events": [{"title": "standup", "time": "10:00"}]}

    async def cal_update(p, ctx, *, dry_run):
        if not dry_run:
            calls.append("cal.update")
        return {"would_update": p} if dry_run else {"updated": True, **p}

    async def mail_draft(p, ctx, *, dry_run):
        return {"would_draft": True} if dry_run else {"draft": p["body"]}

    async def hook(aid, card):
        return broker.approve(aid, HUMAN)

    gw = SlowPlannerGateway()
    executor = Executor(ledger=ledger, guard=guard, broker=broker, registry=registry, backend=None,
                        natives={"cal.read": cal_read, "cal.update": cal_update, "mail.draft": mail_draft}, approval_hook=hook)
    selector = SkillSelector(gw)

    def hashes(skills):
        return {s: (ledger.get_skill(s) or {}).get("tree_hash", "") for s in skills}

    library = RouteLibrary(routes, skill_hashes=hashes)

    def make_agent(trust="user_direct", l1=None, l2=None, calibrator=None, library_on=True):
        cal = calibrator or Calibrator(default=Calib(1.0, 0.85))
        cascade = Cascade(gateway=gw, selector=selector, l1=l1 or FakeBackend(fixed={"needs_plan": (True, 0.9)}), l2=l2 or FakeBackend(fixed={"needs_plan": (True, 0.9)}),
                          calibrator=cal, library=library if library_on else None, family_skills=FAMILY)
        reader = MemoryReader(mem.db_path, keys)
        agent = Agent(mem=mem, ledger=ledger, reader=reader, index=VectorIndex(), gateway=gw, cascade=cascade, planner=Planner(gw, lambda n: _manifest(registry, n)),
                      executor=executor, registry=registry, library=library if library_on else None, routes=routes,
                      ctx=ExecContext(compartment="work", workspace=ws, data_dir=data, today="2026-10-25", base_trust=trust))
        return agent

    class W:
        pass

    w = W()
    w.__dict__.update(dict(mem=mem, ledger=ledger, routes=routes, library=library, gw=gw, registry=registry, make_agent=make_agent, selector=selector,
                           calls=calls, guard=guard, data=data, src=src, hashes=hashes))
    yield w
    mem.close()
    set_clock(RealClock())


def _manifest(registry, name):
    try:
        return registry.verify_runnable(name).manifest
    except Exception:  # noqa: BLE001
        return None


async def prime(w):
    await w.selector.index([_manifest(w.registry, n) for n in ("cal.read", "cal.update", "mail.draft")], get_clock().monotonic() + 10)


REQ = "Reschedule my standup to {t} and tell the attendees"


async def test_ROUTE_learning_curve_descends_from_L3_to_L0_and_gets_faster(world):
    await prime(world)
    agent = world.make_agent()
    tiers, lats, learned = [], [], []
    for t in ("4pm", "5pm", "3pm", "6pm", "2pm", "7pm", "1pm", "8pm"):
        r = await agent.handle(REQ.format(t=t))
        assert r.outcome and r.outcome.state == "succeeded", (t, r.outcome and r.outcome.errors)
        tiers.append(r.tier)
        lats.append(r.latency_ms)
        learned.append(r.learned)
    assert tiers == ["L3", "L3", "L3", "L0", "L0", "L0", "L0", "L0"], tiers
    assert learned[0] == "candidate_created" and learned[1] == "shadow_agree" and learned[2] == "promoted"
    l3 = sorted(lats[:3])[1]
    l0 = sorted(lats[3:])[len(lats[3:]) // 2]
    assert l0 < l3, f"learned routes must be faster: L0 {l0:.0f} ms vs L3 {l3:.0f} ms"
    assert world.gw.planner_calls == 3 * 2, "after promotion the big model is never called for this task family"
    route = world.routes.list(state="active")[0]
    assert route["successes"] == 5 and route["shadow_agree"] == 2


async def test_L0_instantiates_the_new_values_into_the_recipe(world):
    await prime(world)
    agent = world.make_agent()
    for t in ("4pm", "5pm", "3pm"):
        await agent.handle(REQ.format(t=t))
    world.calls.clear()
    r = await agent.handle(REQ.format(t="9pm"))
    assert r.tier == "L0" and r.outcome.results["n2"]["start"] == "21:00"
    assert r.outcome.results["n3"]["draft"] == "Standup moved to 21:00"


async def test_ROUTE_poison_never_promoted_when_the_task_touched_untrusted_content(world):
    await prime(world)
    agent = world.make_agent(trust="received")
    for t in ("4pm", "5pm", "3pm", "6pm"):
        r = await agent.handle(REQ.format(t=t), trust="received")
        assert r.tier == "L3" and r.learned is None
    assert world.routes.list() == [], "tainted tasks never produce a route (gate P4)"
    assert any(e["kind"] == "candidate_rejected" and e["tier"] == "P4" for e in world.routes.events())


async def test_ROUTE_shadow_requires_2_agreements_and_a_disagreement_resets(world):
    lib = world.library
    rid = world.routes.insert(compartment="work", signature_text="x", intent_family="g", recipe={"nodes": [{"id": "a", "skill": "cal.read", "params": {}}]},
                              risk_tier_hint=0, trust_floor="agent", tree_hash="h", created_from_task=None, state="shadow")
    assert lib.record_shadow(rid, True) == "shadow"
    assert lib.record_shadow(rid, False) == "shadow" and world.routes.get(rid)["shadow_agree"] == 0
    assert lib.record_shadow(rid, True) == "shadow"
    assert lib.record_shadow(rid, True) == "active"


async def test_ROUTE_demotion_on_failure_and_when_a_skill_changes(world):
    await prime(world)
    agent = world.make_agent()
    for t in ("4pm", "5pm", "3pm"):
        await agent.handle(REQ.format(t=t))
    rid = world.routes.list(state="active")[0]["id"]
    now = get_clock().now_ms()
    world.library.record_outcome(rid, False, now)
    world.library.record_outcome(rid, False, now)
    assert world.routes.get(rid)["state"] == "demoted"
    world.library.reverify(rid)
    assert world.routes.get(rid)["state"] == "shadow"


async def test_ROUTE_hash_change_demotes(world):
    await prime(world)
    agent = world.make_agent()
    for t in ("4pm", "5pm", "3pm"):
        await agent.handle(REQ.format(t=t))
    assert world.routes.list(state="active")
    (world.data / "skills" / "cal.update" / "manifest.json").write_text(
        (world.data / "skills" / "cal.update" / "manifest.json").read_text(encoding="utf-8").replace("moved to a new time", "moved to a different time"), encoding="utf-8")
    world.registry.register(world.data / "skills" / "cal.update", builtin=True)  # re-registration changes the recorded hash
    r = await agent.handle(REQ.format(t="9pm"))
    assert r.tier != "L0" and world.routes.list(state="active") == [], "the route was demoted because a skill it binds changed"


async def test_ROUTE_cannot_store_preapproval_only_a_recipe(world):
    await prime(world)
    agent = world.make_agent()
    for t in ("4pm", "5pm", "3pm"):
        await agent.handle(REQ.format(t=t))
    row = world.routes.list(state="active")[0]
    recipe = json.loads(row["recipe_json"])
    assert set(recipe) == {"nodes", "example_request"}
    assert all(set(n) <= {"id", "skill", "params", "depends_on", "criteria"} for n in recipe["nodes"])
    assert "approval" not in row["recipe_json"].lower() and "token" not in row["recipe_json"].lower()
    r = await agent.handle(REQ.format(t="9pm"))
    assert r.tier == "L0" and r.outcome.state == "succeeded"
    assert world.calls.count("cal.update") == 4, "the L0 run still executed the gated action itself, through the guard and the broker"


async def test_L0_hit_skips_models(world):
    await prime(world)
    agent = world.make_agent()
    for t in ("4pm", "5pm", "3pm"):
        await agent.handle(REQ.format(t=t))
    before = world.gw.planner_calls
    seen = []

    class Spy(FakeBackend):
        async def decide(self, *a, **k):
            seen.append(1)
            return await super().decide(*a, **k)

    agent2 = world.make_agent(l1=Spy(), l2=Spy())
    r = await agent2.handle(REQ.format(t="11pm"))
    assert r.tier == "L0" and world.gw.planner_calls == before
    assert r.decision.backend == "route_library"


async def test_laya_abstain_escalates_to_L2_and_confident_laya_answers_at_L1(world):
    await prime(world)
    confident = FakeBackend(fixed={"skill_family": ("calendar", 0.97), "needs_plan": (False, 0.97), "skill_in_family": ("cal.read", 0.95)})
    r = await world.make_agent(l1=confident, library_on=False).handle("what is on my calendar today")
    assert r.tier == "L1" and r.decision.skills == ["cal.read"]
    unsure = FakeBackend(fixed={"skill_family": ("calendar", 0.40), "needs_plan": (False, 0.97)})
    arbiter = FakeBackend(fixed={"skill": ("cal.read", 0.9), "needs_plan": (False, 0.9)})
    r2 = await world.make_agent(l1=unsure, l2=arbiter, library_on=False).handle("what is on my calendar today")
    assert r2.tier == "L2" and "L1_abstained" in r2.decision.notes


async def test_laya_cannot_lower_tier_the_guard_still_demands_a_click(world):
    await prime(world)
    says_safe = FakeBackend(fixed={"skill_family": ("calendar", 0.99), "needs_plan": (False, 0.99), "skill_in_family": ("cal.update", 0.99), "risk_precheck": ("none", 0.99)})
    agent = world.make_agent(l1=says_safe, library_on=False)
    clicks: list = []
    orig = agent.executor._hook

    async def counting(aid, card):
        clicks.append(card["skill"])
        return await orig(aid, card)

    agent.executor._hook = counting
    r = await agent.handle("move standup to 4pm")
    assert r.decision.risk_floor == 0 and clicks == ["cal.update"], "the model said 'no risk'; policy still required an approval click"


async def test_interactive_timeout_uses_the_embedding_candidate_within_budget(world):
    await prime(world)

    class Slow(FakeBackend):
        async def decide(self, *a, **k):
            await asyncio.sleep(3)
            return []

    import time

    t0 = time.perf_counter()
    d = await world.make_agent(l1=Slow(), l2=Slow(), library_on=False).cascade.route("do the thing with the calendar", compartment="work", caller_trust="user_direct", now_ms=get_clock().now_ms())
    elapsed = (time.perf_counter() - t0) * 1000
    assert elapsed < 1500 and d.tier in ("L2", "L3")
    assert d.low_confidence or d.tier == "L3"
