"""NFR3.1 graceful degradation and NFR3.2 state recovery, through the real wiring."""

from __future__ import annotations

import asyncio
import time

import pytest

from ragnarok.connectors.base import Cursor, ItemRef, RawItem, SyncResult
from ragnarok.connectors.runner import ConnectorRunner
from ragnarok.core.clock import RealClock, set_clock
from ragnarok.core.errors import ModelError
from ragnarok.daemon.chat import summarize
from ragnarok.daemon.runtime import Governor, Runtime
from ragnarok.daemon.wiring import build_world, warm_up
from ragnarok.harness.executor import ExecContext
from ragnarok.harness.plan import Plan, PlanNode
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.memory.queue import IngestQueue
from ragnarok.memory.runtime_state import RuntimeState
from ragnarok.platform.secrets import MemoryStore


@pytest.fixture(autouse=True)
def clock():
    set_clock(RealClock())


async def test_an_interrupted_task_is_resumed_after_a_restart_in_well_under_five_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "home"))
    vault = MemoryStore()
    a = build_world(vault=vault, gateway=FakeGateway(), sandbox=False, use_laya=False, max_cpu=101)
    ctx: ExecContext = a.agent.ctx
    plan = Plan(nodes=[PlanNode(id="n1", goal="remind", skill="reminder.add", params={"text": "survive the crash", "when": ""}, criteria=[{"kind": "field_present", "path": "reminder_id"}])])
    tid = a.agent.executor.start_task("remind me to survive the crash", plan, ctx)
    nid = next(n["id"] for n in a.ledger.nodes_of(tid))
    a.ledger.set_node_state(nid, "running")  # the process "died" while the node was running
    a.mem.close()
    t0 = time.perf_counter()
    b = build_world(vault=vault, gateway=FakeGateway(), sandbox=False, use_laya=False, max_cpu=101)
    await warm_up(b)
    restore_s = time.perf_counter() - t0
    for _ in range(100):
        if b.ledger.get_task(tid)["state"] in ("succeeded", "failed", "partial"):
            break
        await asyncio.sleep(0.05)
    assert b.recovery.resumed == [tid] and b.recovery.requeued_nodes == 1
    assert b.ledger.get_task(tid)["state"] == "succeeded"
    assert [r["what"] for r in b.services.runtime.commitments.list_open()] == ["survive the crash"], "the work was finished exactly once"
    assert restore_s < 5.0
    b.mem.close()


class Broken:
    name = "broken"

    def describe(self):
        return None

    async def sync(self, cursor):
        raise ConnectionError("down")

    async def fetch(self, ref):
        return None

    def actions(self):
        return []

    async def health(self):
        return None


class Good:
    name = "good"

    def describe(self):
        return None

    async def sync(self, cursor):
        return SyncResult([ItemRef("1")], Cursor({"n": 1}))

    async def fetch(self, ref):
        return RawItem("good", ref.native_id, "a perfectly fine message about lunch", "received")

    def actions(self):
        return []

    async def health(self):
        return None


async def test_one_failing_source_does_not_stop_the_others(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "home"))
    w = build_world(vault=MemoryStore(), gateway=FakeGateway(), sandbox=False, use_laya=False, max_cpu=101)
    q = IngestQueue(w.mem)
    rt = Runtime(RuntimeState(w.mem), {"broken": ConnectorRunner(w.mem, q, Broken(), "work"), "good": ConnectorRunner(w.mem, q, Good(), "work", gateway=w.gateway)}, governor=Governor(101))
    out = await rt.tick()
    assert "good" in out and out["good"].ingested == 1
    assert rt.last["broken"]["ok"] is False and any(a["kind"] == "error" and a["source"] == "broken" for a in rt.activity)
    w.mem.close()


class DownGateway(FakeGateway):
    async def chat(self, **kw):
        raise ModelError("ollama is not running", code="E_MODEL_UNAVAILABLE", retryable=True)

    async def embed(self, texts, **kw):
        raise ModelError("ollama is not running", code="E_MODEL_UNAVAILABLE", retryable=True)


async def test_with_the_model_server_down_the_agent_degrades_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "home"))
    w = build_world(vault=MemoryStore(), gateway=DownGateway(), sandbox=False, use_laya=False, max_cpu=101)
    await warm_up(w)
    # deterministic paths need no model at all
    assert "Reminder set" in summarize(await w.agent.handle("remind me to call mom tomorrow at 8am"))["text"]
    assert "calendar" in summarize(await w.agent.handle("what is on my calendar tomorrow"))["text"].lower()
    assert summarize(await w.agent.handle("what is the time right now"))["text"].startswith("It is")
    # paths that need a model say so honestly
    for q in ("hello how are you doing", "what is the budget review about"):
        out = summarize(await w.agent.handle(q))
        assert out["text"] and "Traceback" not in out["text"]
    w.mem.close()
