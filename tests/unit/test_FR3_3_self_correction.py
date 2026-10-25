"""FR3.3: transient failures are retried, a failed read-only plan is adjusted once, and sends are never retried by the machine."""

from __future__ import annotations

import asyncio
import json

import pytest

from ragnarok.connectors.http import ConnectorError
from ragnarok.core.clock import RealClock, set_clock
from ragnarok.daemon.chat import summarize
from ragnarok.daemon.wiring import build_world, warm_up
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.platform.secrets import MemoryStore

calls = {"n": 0, "post": 0}


def responder(messages, schema):
    props = (schema or {}).get("properties", {})
    if "steps" in props:
        return {"steps": [{"goal": "look it up", "skill": "memory.search"}]}
    if "nodes" in props:
        return {"nodes": [{"id": "n1", "goal": "look it up", "skill": "memory.search", "params": {"query": "standup"}, "depends_on": [], "criteria": [{"kind": "ok"}]}]}
    return {}


@pytest.fixture
def world(tmp_path, monkeypatch):
    set_clock(RealClock())
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "home"))
    calls.update(n=0, post=0)
    w = build_world(vault=MemoryStore(), gateway=FakeGateway(responder), sandbox=False, use_laya=False, embed_rungs=False, max_cpu=101)
    yield w
    w.mem.close()


async def test_a_transient_failure_of_a_read_only_step_is_retried_and_then_succeeds(world, monkeypatch):
    await warm_up(world)
    monkeypatch.setattr("ragnarok.harness.executor.BACKOFF_S", 0.0)  # no real backoff in tests

    async def flaky(p, ctx, *, dry_run):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectorError("network", code="E_NET", retryable=True)
        return {"reminders": []}

    world.agent.executor._natives["reminder.list"] = flaky
    out = summarize(await world.agent.handle("what reminders do I have"))
    assert calls["n"] == 3 and "Open reminders" in out["text"]


async def test_a_permanent_failure_is_not_retried_but_the_plan_is_adjusted_once(world):
    await warm_up(world)

    async def broken(p, ctx, *, dry_run):
        calls["n"] += 1
        raise RuntimeError("boom")

    world.agent.executor._natives["reminder.list"] = broken
    turn = await world.agent.handle("what reminders do I have")
    assert calls["n"] == 1, "a non-transient error is not repeated blindly"
    assert any("Self-correction" in t for t in turn.decision.trace)
    assert turn.outcome is not None and turn.outcome.state == "succeeded", "the adjusted plan (a memory search) worked"


async def test_a_send_is_never_retried_by_the_machine(world, monkeypatch):
    await warm_up(world)

    async def lossy_send(chat_id, text):
        calls["post"] += 1
        raise ConnectorError("network", code="E_NET", retryable=True)

    from ragnarok.ingest.pipeline import ingest_text

    await ingest_text(world.mem, world.gateway, world.index, source="telegram", native_id="55:1", compartment="work", trust="received", text="hello", title="Ravi")
    world.agent.executor._natives["chat.reply"].__self__._senders = {"telegram": lossy_send}
    task = asyncio.create_task(world.agent.handle("tell Ravi on telegram that I am late"))
    for _ in range(100):
        for c in world.services.hub.pending():
            world.services.hub.decide(c["id"], True)
        if task.done():
            break
        await asyncio.sleep(0.02)
    turn = await asyncio.wait_for(task, 5)
    assert calls["post"] == 1, "one attempt only: a lost reply may mean it was delivered"
    assert json.dumps(turn.decision.trace).count("Self-correction") == 0
