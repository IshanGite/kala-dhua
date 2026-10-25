"""NFR4.2: permanent deletion with cascade, preview, confirmation, receipt and a verification scan that finds nothing."""

from __future__ import annotations

import json

import pytest

from ragnarok.core.clock import RealClock, SimClock, set_clock
from ragnarok.ingest.pipeline import ingest_text
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.memory.delete import ConfirmRequired, Deleter, DeleteSelector
from ragnarok.memory.models import EventIn, FactIn, ProvenanceIn
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.service import MemoryService
from ragnarok.store.vectors import VectorIndex

SECRET_A = "The Zephyrine acquisition closes on the ninth of March with a purchase price of forty million rupees"
TEXT_A = f"Project note.\n\n{SECRET_A}. Legal counsel is Marguerite Okonkwo-Bell at the Delhi office."
TEXT_B = "Vendor lunch is on Friday at the canteen near the Gurugram office and everyone is invited."


@pytest.fixture
def world(tmp_path, keys):
    set_clock(SimClock(1_760_000_000_000))
    mem = MemoryService(tmp_path / "db" / "d.db", keys)
    yield mem, keys
    mem.close()
    set_clock(RealClock())


async def seed(mem, keys):
    gw, idx = FakeGateway(), VectorIndex()
    a = await ingest_text(mem, gw, idx, source="files", native_id="zeph.md", compartment="work", trust="user_file", text=TEXT_A, title="Zephyrine")
    b = await ingest_text(mem, gw, idx, source="files", native_id="lunch.md", compartment="work", trust="user_file", text=TEXT_B, title="Lunch")
    reader = MemoryReader(mem.db_path, keys)
    cid = reader.compartment_id("work")
    chunk_a = reader.fts_chunks("Zephyrine acquisition", cid)[0]["id"]
    chunk_b = reader.fts_chunks("Vendor lunch", cid)[0]["id"]
    # fact 1: only evidence is item A; fact 2: evidence in A AND B; a summary built from A
    only_a = mem.write_fact(FactIn(compartment="work", kind="knowledge", subject="Zephyrine", predicate="closing_date", value="ninth of March", confidence=0.9, trust="user_file"),
                            [ProvenanceIn(parent_type="chunk", parent_id=chunk_a, quote="closes on the ninth of March")])
    both = mem.write_fact(FactIn(compartment="work", kind="knowledge", subject="Office", predicate="city", value="Delhi and Gurugram", confidence=0.8, trust="user_file"),
                          [ProvenanceIn(parent_type="chunk", parent_id=chunk_a, quote="Delhi office"), ProvenanceIn(parent_type="chunk", parent_id=chunk_b, quote="Gurugram office")])
    mem.put_embedding("fact", only_a.id, "work", "fake", (await gw.embed(["Zephyrine closing"], deadline=1e12, priority=0))[0])

    def add_saga(c):
        c.execute("INSERT INTO saga(id,compartment_id,period,summary) VALUES ('s1',?, '2026-W10', 'This week the Zephyrine acquisition was finalised for forty million rupees')", (cid,))
        c.execute("INSERT INTO provenance(child_type,child_id,parent_type,parent_id,relation) VALUES ('saga','s1','chunk',?, 'summarises')", (chunk_a,))
        c.execute("INSERT INTO blob_keys(blob_id,item_id,wrapped_key,path,size,created_at) VALUES ('b1',?, x'00', '/nonexistent/blob', 10, 1)", (a.item_id,))

    mem.with_tx(add_saga)
    return a, b, only_a, both, idx


async def test_NFR4_2_delete_proof_zero_residue_and_the_rest_survives(world):
    mem, keys = world
    a, b, only_a, both, _ = await seed(mem, keys)
    d = Deleter(mem)
    sel = DeleteSelector(item_ids=(a.item_id,))
    prev = d.preview(sel)
    assert prev.counts["items"] == 1 and prev.counts["facts_removed"] == 1 and prev.counts["facts_reflagged"] == 1
    assert prev.counts["summaries"] == 1 and prev.counts["blobs"] == 1 and prev.counts["embeddings"] >= a.chunks
    assert "permanently remove" in prev.summary and "re-confirmation" in prev.summary

    receipt = d.delete(sel, prev.confirm_token)
    assert receipt.verified and receipt.residual == 0 and receipt.fingerprints > 0
    reader = MemoryReader(mem.db_path, keys)
    cid = reader.compartment_id("work")
    assert reader.fts_chunks("Zephyrine acquisition", cid) == [] and reader.fts_chunks("Marguerite Okonkwo-Bell", cid) == []
    assert reader.count("facts") == 1, "the fact whose only evidence was deleted is gone; the one with other evidence stays"
    survivor = reader.facts_by_state(cid, "needs_confirmation")
    assert [f["id"] for f in survivor] == [both.id], "it keeps its remaining evidence but must be re-confirmed"
    assert reader.provenance_of("fact", both.id) and all(p["parent_type"] != "item" for p in reader.provenance_of("fact", both.id))
    assert [r["text"] for r in reader.fts_chunks("Vendor lunch", cid)], "unrelated memory is untouched"
    assert reader._r.execute("SELECT count(*) FROM saga").fetchone()[0] == 0
    assert reader._r.execute("SELECT count(*) FROM blob_keys").fetchone()[0] == 0, "the per-file key is destroyed (crypto-shredding)"
    assert reader._r.execute("SELECT count(*) FROM embeddings WHERE owner_type='fact'").fetchone()[0] == 0


async def test_NFR4_2_receipt_and_tombstone_hold_counts_only_never_content(world):
    mem, keys = world
    a, *_ = await seed(mem, keys)
    d = Deleter(mem)
    sel = DeleteSelector(item_ids=(a.item_id,))
    receipt = d.delete(sel, d.preview(sel).confirm_token)
    reader = MemoryReader(mem.db_path, keys)
    tomb = reader._r.execute("SELECT * FROM tombstones WHERE id=?", (receipt.tombstone_id,)).fetchone()
    log = reader._r.execute("SELECT counts_json, reason_code FROM change_log").fetchall()
    blob = json.dumps([dict(tomb), [tuple(r) for r in log]])
    assert "Zephyrine" not in blob and "Okonkwo" not in blob and json.loads(tomb["counts_json"])["items"] == 1


async def test_two_step_rule_the_delete_needs_the_exact_confirmation(world):
    mem, keys = world
    a, b, *_ = await seed(mem, keys)
    d = Deleter(mem)
    sel = DeleteSelector(item_ids=(a.item_id,))
    with pytest.raises(ConfirmRequired):
        d.delete(sel, "")
    with pytest.raises(ConfirmRequired):
        d.delete(sel, d.preview(DeleteSelector(item_ids=(b.item_id,))).confirm_token)  # a token for a DIFFERENT selection is worthless
    assert MemoryReader(mem.db_path, keys).count("items") == 2, "nothing was deleted by the refused attempts"


async def test_a_deleted_source_cannot_silently_come_back_until_the_user_says_so(world):
    mem, keys = world
    a, *_ = await seed(mem, keys)
    d = Deleter(mem)
    sel = DeleteSelector(item_ids=(a.item_id,))
    d.delete(sel, d.preview(sel).confirm_token)
    again = await ingest_text(mem, FakeGateway(), None, source="files", native_id="zeph.md", compartment="work", trust="user_file", text=TEXT_A, title="Zephyrine")
    assert again.action == "excluded"
    mem.clear_exclusion("files", "zeph.md")
    back = await ingest_text(mem, FakeGateway(), None, source="files", native_id="zeph.md", compartment="work", trust="user_file", text=TEXT_A, title="Zephyrine")
    assert back.action == "inserted"


async def test_delete_everything_from_one_source(world):
    mem, keys = world
    await seed(mem, keys)
    d = Deleter(mem)
    sel = DeleteSelector(source="files", compartment="work")
    receipt = d.delete(sel, d.preview(sel).confirm_token)
    assert receipt.counts["items"] == 2 and receipt.verified
    assert MemoryReader(mem.db_path, keys).count("items") == 0


def test_deleting_an_event_relinks_the_hash_chain_so_tamper_detection_still_works(world):
    mem, keys = world
    ids = [mem.write_event(EventIn(compartment="work", kind="note", actor="user", content=f"event number {i} with text")) for i in range(6)]
    d = Deleter(mem)
    sel = DeleteSelector(event_ids=(ids[2],))
    receipt = d.delete(sel, d.preview(sel).confirm_token)
    reader = MemoryReader(mem.db_path, keys)
    assert reader.count("events") == 5 and reader.verify_chain() == (True, None) and receipt.verified


async def test_deleting_a_single_fact_removes_its_vectors_and_provenance(world):
    mem, keys = world
    _, _, only_a, _, _ = await seed(mem, keys)
    d = Deleter(mem)
    sel = DeleteSelector(fact_ids=(only_a.id,))
    receipt = d.delete(sel, d.preview(sel).confirm_token)
    reader = MemoryReader(mem.db_path, keys)
    assert receipt.verified and reader.provenance_of("fact", only_a.id) == []
    assert reader._r.execute("SELECT count(*) FROM facts WHERE id=?", (only_a.id,)).fetchone()[0] == 0
