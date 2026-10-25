"""NFR4.2 through the API: see, edit, export and permanently delete memory with a preview, a confirmation token and a verified receipt; NFR4.1: replies explain themselves."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ragnarok.core.clock import RealClock, set_clock
from ragnarok.daemon.api import Services, create_app
from ragnarok.daemon.auth import HEADER, TokenGuard
from ragnarok.harness.memorycmd import MemoryCommands
from ragnarok.ingest.pipeline import ingest_text
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.memory.models import EventIn
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.service import MemoryService
from ragnarok.store.vectors import VectorIndex


@pytest.fixture
def world(tmp_path, keys, monkeypatch):
    set_clock(RealClock())
    monkeypatch.setenv("HOME", str(tmp_path))  # exports go to a throwaway Downloads
    (tmp_path / "Downloads").mkdir()
    mem = MemoryService(tmp_path / "db" / "d.db", keys)
    reader = MemoryReader(mem.db_path, keys)
    reindexed = []
    app = create_app(TokenGuard("tok"), 8765, Services(reader=reader, mem=mem, reindex=lambda: reindexed.append(1)))
    c = TestClient(app, base_url="http://127.0.0.1:8765", headers={HEADER: "tok"})
    yield mem, reader, c, reindexed, tmp_path
    mem.close()


async def seed(mem):
    a = await ingest_text(mem, FakeGateway(), VectorIndex(), source="discord", native_id="g:1", compartment="work", trust="received", text="The Zephyrine acquisition closes on the ninth of March for forty million rupees.", title="deal")
    await ingest_text(mem, FakeGateway(), VectorIndex(), source="discord", native_id="g:2", compartment="work", trust="received", text="Lunch is on Friday at the canteen near the office.", title="lunch")
    return a


async def test_delete_needs_the_preview_token_and_leaves_nothing_behind(world):
    mem, reader, c, reindexed, _ = world
    a = await seed(mem)
    assert c.post("/memory/delete", json={"item_ids": [a.item_id]}).json() == {"ok": False, "error": "E_CONFIRM_REQUIRED"}
    p = c.post("/memory/delete/preview", json={"item_ids": [a.item_id]}).json()
    assert p["counts"]["items"] == 1 and "permanently" in p["summary"]
    r = c.post("/memory/delete", json={"item_ids": [a.item_id], "token": p["token"]}).json()
    assert r["ok"] and r["verified"] and r["residual"] == 0 and reindexed
    assert reader.fts_chunks("Zephyrine acquisition", reader.compartment_id("work")) == []
    assert reader.fts_chunks("Lunch canteen", reader.compartment_id("work")), "unrelated memory untouched"


async def test_remembered_facts_can_be_listed_edited_and_forgotten(world):
    mem, reader, c, _, _ = world
    eid = mem.write_event(EventIn(compartment="work", kind="user_message", actor="user", content="remember that I prefer morning meetings"))
    MemoryCommands(mem, reader).remember("remember that I prefer morning meetings", eid)
    f = c.get("/memory/facts").json()["items"][0]
    assert f["kind"] == "preference" and f["evidence"] >= 1
    assert c.post(f"/memory/facts/{f['id']}", json={"value": "afternoon meetings"}).json()["ok"]
    assert c.get("/memory/facts").json()["items"][0]["value"] == "afternoon meetings" and c.get("/memory/facts").json()["items"][0]["pinned"]
    p = c.post("/memory/delete/preview", json={"fact_ids": [f["id"]]}).json()
    assert c.post("/memory/delete", json={"fact_ids": [f["id"]], "token": p["token"]}).json()["ok"]
    assert c.get("/memory/facts").json()["items"] == []


async def test_export_writes_a_file_with_everything_and_its_provenance(world):
    mem, reader, c, _, tmp = world
    await seed(mem)
    eid = mem.write_event(EventIn(compartment="work", kind="user_message", actor="user", content="remember that my favourite colour is blue"))
    MemoryCommands(mem, reader).remember("remember that my favourite colour is blue", eid)
    j = c.post("/memory/export?fmt=json").json()
    data = json.loads(open(j["path"], encoding="utf-8").read())
    assert j["items"] == 2 and j["facts"] == 1 and data["facts"][0]["provenance"] and "Zephyrine" in json.dumps(data["items"])
    md = c.post("/memory/export?fmt=md").json()
    assert open(md["path"], encoding="utf-8").read().startswith("# Ragnarok memory export")
