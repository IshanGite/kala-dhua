"""P3: local-file connector through the runner: ingest, change detection, crash re-delivery, pairing, dead letters."""

from __future__ import annotations

import time

import pytest

from ragnarok.connectors.base import Cursor, ItemRef, RawItem, SyncResult
from ragnarok.connectors.local_files import LocalFiles
from ragnarok.connectors.runner import ConnectorRunner
from ragnarok.core.clock import RealClock, set_clock
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.memory.queue import IngestQueue
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.service import MemoryService


@pytest.fixture
def world(tmp_path, keys):
    set_clock(RealClock())
    mem = MemoryService(tmp_path / "db" / "d.db", keys)
    docs = tmp_path / "docs"
    docs.mkdir()
    yield mem, keys, docs
    mem.close()


async def test_files_are_ingested_changes_detected_and_hidden_skipped(world):
    mem, keys, docs = world
    (docs / "plan.md").write_text("Quarterly plan for the Saturn launch is due on Friday.", encoding="utf-8")
    (docs / ".secret.md").write_text("hidden file content", encoding="utf-8")
    (docs / "photo.xyz").write_text("unsupported", encoding="utf-8")
    run = ConnectorRunner(mem, IngestQueue(mem), LocalFiles([docs]), "work", gateway=FakeGateway())
    rep = await run.cycle()
    assert rep.ingested == 1 and rep.failed == 0
    reader = MemoryReader(mem.db_path, keys)
    assert reader.fts_chunks("Saturn launch", reader.compartment_id("work"))
    again = await run.cycle()
    assert again.queued == 0, "nothing changed, nothing is re-read"
    time.sleep(0.02)
    (docs / "plan.md").write_text("Quarterly plan for the Saturn launch moved to Monday.", encoding="utf-8")
    third = await run.cycle()
    assert third.ingested == 1
    assert reader.fts_chunks("moved to Monday", reader.compartment_id("work"))
    assert reader.fts_chunks("due on Friday", reader.compartment_id("work")) == [], "the old version was replaced, not duplicated"


async def test_cursor_survives_restart_and_a_crash_before_drain_loses_nothing(world):
    mem, keys, docs = world
    (docs / "a.txt").write_text("Alpha document about the harbour project.", encoding="utf-8")
    q = IngestQueue(mem)
    first = ConnectorRunner(mem, q, LocalFiles([docs]), "work")
    await first.pull()  # crash here: queued but never drained
    q.recover()
    second = ConnectorRunner(mem, IngestQueue(mem), LocalFiles([docs]), "work")
    rep = await second.drain()
    assert rep.ingested == 1
    assert (await second.pull()).queued == 0, "the cursor was persisted"


class FakeChat:
    name = "slackfake"

    def describe(self):
        return None

    async def sync(self, cursor):
        return SyncResult([ItemRef("m1", {"sender": "U_STRANGER"}), ItemRef("m2", {"sender": "U_FRIEND"}), ItemRef("m3", {"sender": "U_BAD"})], Cursor({"ts": 3}))

    async def fetch(self, ref):
        if ref.native_id == "m9":
            raise RuntimeError("boom")
        return RawItem("slackfake", ref.native_id, f"message {ref.native_id} about lunch plans", "received")

    def actions(self):
        return []

    async def health(self):
        return None


async def test_unknown_senders_are_held_until_paired_and_blocked_ones_never_enter(world):
    mem, keys, _ = world
    q = IngestQueue(mem)
    run = ConnectorRunner(mem, q, FakeChat(), "work", require_pairing=True)
    run.block_sender("U_BAD")
    run.approve_sender("U_FRIEND")
    rep = await run.cycle()
    assert rep.held == 1 and rep.queued == 1 and rep.ingested == 1
    reader = MemoryReader(mem.db_path, keys)
    cid = reader.compartment_id("work")
    assert len(reader.fts_chunks("m2", cid)) == 1 and reader.fts_chunks("m1", cid) == [] and reader.fts_chunks("m3", cid) == []
    assert run.approve_sender("U_STRANGER") == 1
    assert (await run.drain()).ingested == 1


async def test_a_poisoned_item_is_dead_lettered_and_does_not_stall_the_rest(world):
    mem, _, _ = world
    q = IngestQueue(mem)
    q.enqueue("slackfake", "m9")
    q.enqueue("slackfake", "ok1")
    run = ConnectorRunner(mem, q, FakeChat(), "work")
    for _ in range(3):
        await run.drain()
    assert q.stats().get("dead") == 1 and q.stats().get("done") == 1
