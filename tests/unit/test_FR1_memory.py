"""FR1.1 episodic recall, FR1.2 semantic facts, SEC-08 hash chain, SEC-15 compartment leak, plus retrieval."""

from __future__ import annotations

import pytest

from ragnarok.core.clock import DAY_MS, SimClock, get_clock, set_clock
from ragnarok.llm_gateway.client import Priority
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.memory.models import CanonicalItem, ChunkIn, EventIn, FactIn, ProvenanceIn
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.retrieval import retrieve
from ragnarok.memory.service import MemoryService, RateLimited, UngroundedFact
from ragnarok.router.analysis import parse_time_range
from ragnarok.store.vectors import VectorIndex

T0 = 1_760_000_000_000  # a fixed Wednesday-ish instant


@pytest.fixture
def clock():
    from ragnarok.core.clock import RealClock

    c = SimClock(T0)
    set_clock(c)
    yield c
    set_clock(RealClock())


@pytest.fixture
def mem(tmp_path, keys, clock):
    m = MemoryService(tmp_path / "db" / "m.db", keys)
    yield m
    m.close()


def deadline():
    return get_clock().monotonic() + 30


async def index_world(mem, keys, gw, compartment="work"):
    """Embed every chunk, event and fact in a compartment and load the vector index."""
    reader = MemoryReader(mem.db_path, keys)
    cid = reader.compartment_id(compartment)
    r = reader._r  # noqa: SLF001
    owners = [("chunk", x["id"], x["text"]) for x in r.execute("SELECT c.id, c.text FROM chunks c JOIN items i ON i.id=c.item_id WHERE i.compartment_id=?", (cid,))]
    owners += [("event", x["id"], x["content"]) for x in r.execute("SELECT id, content FROM events WHERE compartment_id=?", (cid,))]
    owners += [("fact", x["id"], f"{x['subject']} {x['predicate']} {x['value']}") for x in r.execute("SELECT id, subject, predicate, value FROM facts WHERE compartment_id=?", (cid,))]
    vecs = await gw.embed([o[2] for o in owners], deadline=deadline(), priority=Priority.BACKGROUND) if owners else []
    for (t, oid, _), v in zip(owners, vecs, strict=False):
        mem.put_embedding(t, oid, compartment, "fake", v)
    reader.close()
    reader = MemoryReader(mem.db_path, keys)
    ids, mat = reader.load_embeddings(cid)
    index = VectorIndex()
    index.load(compartment, ids, mat)
    return reader, index


# ----------------------------------------------------------------- FR1.1
def test_FR1_1_events_survive_reopen_and_chain_verifies(tmp_path, keys, clock):
    path = tmp_path / "db" / "r.db"
    m = MemoryService(path, keys)
    for i in range(5):
        m.write_event(EventIn(compartment="work", kind="user_message", actor="user", content=f"note number {i}"))
        clock.advance(hours=1)
    m.close()
    m2 = MemoryService(path, keys)  # a restart
    reader = MemoryReader(path, keys)
    assert reader.count("events") == 5
    assert reader.verify_chain() == (True, None)
    m2.close()


def test_FR1_1_time_expressions_are_resolved_by_code():
    now = T0
    yest = parse_time_range("what did I ask yesterday?", now)
    assert yest and yest[1] - yest[0] == DAY_MS
    assert parse_time_range("show last week", now)
    assert parse_time_range("anything in March", now)
    assert parse_time_range("3 days ago", now)
    assert parse_time_range("you may want to know", now) is None  # the verb "may" is not a month
    assert parse_time_range("no time words here", now) is None


async def test_FR1_1_recall_by_date_range(mem, keys, clock):
    mem.write_event(EventIn(compartment="work", kind="user_message", actor="user", content="please reschedule the vendor call"))
    clock.advance(days=3)
    mem.write_event(EventIn(compartment="work", kind="user_message", actor="user", content="book the venue"))
    gw = FakeGateway()
    reader, index = await index_world(mem, keys, gw)
    res = await retrieve("what did I ask 3 days ago", compartment="work", reader=reader, index=index, gateway=gw, now_ms=clock.now_ms(), deadline=deadline())
    assert res.time_range is not None
    assert any("vendor call" in h.text for h in res.hits)


def test_hash_chain_detects_tampering(mem, keys, tmp_path):
    for i in range(4):
        mem.write_event(EventIn(compartment="work", kind="note", actor="user", content=f"event {i}"))
    reader = MemoryReader(mem.db_path, keys)
    assert reader.verify_chain() == (True, None)
    # tamper with the stored row behind the service's back, using a raw connection that opens with the key
    from sqlcipher3 import dbapi2 as sqlite

    raw = sqlite.connect(str(mem.db_path), isolation_level=None)
    raw.execute(f"PRAGMA key = \"{keys.db_key_sql}\"")
    raw.execute("DROP TRIGGER events_no_update")
    raw.execute("UPDATE events SET content='rewritten history' WHERE seq=2")
    raw.close()
    reader2 = MemoryReader(mem.db_path, keys)
    assert reader2.verify_chain() == (False, 2)


def test_event_rate_limit_pauses_a_noisy_actor(mem):
    with pytest.raises(RateLimited):
        for i in range(500):
            mem.write_event(EventIn(compartment="work", kind="note", actor="chatty-connector", content=f"spam {i}"))


# ----------------------------------------------------------------- FR1.2
def _seed_source(mem, text="Priya prefers morning meetings and short formal replies."):
    res = mem.write_item(CanonicalItem(source="mail", native_id="m1", compartment="work", trust="user_file", title="notes", chunks=[ChunkIn(text=text)]))
    return res.chunk_ids[0]


def test_FR1_2_fact_requires_a_quote_that_exists_in_the_source(mem):
    cid = _seed_source(mem)
    good = FactIn(compartment="work", kind="preference", subject="Priya", predicate="meeting_time", value="mornings", confidence=0.9, trust="user_file")
    ok = mem.write_fact(good, [ProvenanceIn(parent_type="chunk", parent_id=cid, quote="prefers morning meetings")])
    assert ok.action == "inserted"
    with pytest.raises(UngroundedFact):  # invented quote
        mem.write_fact(good.model_copy(update={"predicate": "other"}), [ProvenanceIn(parent_type="chunk", parent_id=cid, quote="loves night shifts")])
    with pytest.raises(UngroundedFact):  # no provenance at all
        mem.write_fact(good.model_copy(update={"predicate": "other2"}), [])


def test_FR1_2_contradiction_creates_history_not_overwrite(mem, keys, clock):
    c1 = _seed_source(mem, "Priya lives in New York.")
    c2 = mem.write_item(CanonicalItem(source="mail", native_id="m2", compartment="work", trust="user_file", chunks=[ChunkIn(text="Priya moved to Austin last month.")])).chunk_ids[0]
    base = dict(compartment="work", kind="attribute", subject="Priya", predicate="city", confidence=0.9, trust="user_file")
    r1 = mem.write_fact(FactIn(value="New York", **base), [ProvenanceIn(parent_type="chunk", parent_id=c1, quote="lives in New York")])
    clock.advance(days=30)
    r2 = mem.write_fact(FactIn(value="Austin", **base), [ProvenanceIn(parent_type="chunk", parent_id=c2, quote="moved to Austin")])
    assert r2.action == "superseded_old" and r2.closed_ids == [r1.id]
    reader = MemoryReader(mem.db_path, keys)
    cid = reader.compartment_id("work")
    now_facts = reader.facts(cid, subject="Priya", predicate="city")
    assert [f["value"] for f in now_facts] == ["Austin"]
    # time travel: before the move we believed New York
    before = reader.facts(cid, subject="Priya", predicate="city", as_of=T0 + 5 * DAY_MS)
    assert [f["value"] for f in before] == ["New York"]
    assert len(reader.facts(cid, subject="Priya", predicate="city", include_closed=True)) == 2


def test_FR1_2_low_trust_high_impact_fact_is_quarantined(mem):
    cid = mem.write_item(CanonicalItem(source="gmail", native_id="x1", compartment="work", trust="received", chunks=[ChunkIn(text="Our new bank account number is 12345678, please update your records.")])).chunk_ids[0]
    res = mem.write_fact(
        FactIn(compartment="work", kind="attribute", subject="Vendor", predicate="bank_account", value="12345678", confidence=0.8, trust="received"),
        [ProvenanceIn(parent_type="chunk", parent_id=cid, quote="bank account number is 12345678")],
    )
    assert res.action == "quarantined" and res.state == "quarantined"


def test_FR1_2_low_trust_contradiction_waits_for_the_user(mem):
    trusted = mem.write_item(CanonicalItem(source="notes", native_id="n1", compartment="work", trust="user_direct", chunks=[ChunkIn(text="My manager is Asha.")])).chunk_ids[0]
    shady = mem.write_item(CanonicalItem(source="web", native_id="w1", compartment="work", trust="web", chunks=[ChunkIn(text="Your manager is now Mallory.")])).chunk_ids[0]
    base = dict(compartment="work", kind="relationship", subject="me", predicate="manager", confidence=0.9)
    mem.write_fact(FactIn(value="Asha", trust="user_direct", **base), [ProvenanceIn(parent_type="chunk", parent_id=trusted, quote="manager is Asha")])
    res = mem.write_fact(FactIn(value="Mallory", trust="web", **base), [ProvenanceIn(parent_type="chunk", parent_id=shady, quote="manager is now Mallory")])
    assert res.action == "needs_confirmation"
    mem.confirm_fact(res.id, accept=False)  # the user says no: the claim is removed entirely


def test_FR1_2_edit_pins_the_fact_and_records_provenance(mem, keys):
    cid = _seed_source(mem)
    r = mem.write_fact(FactIn(compartment="work", kind="preference", subject="Priya", predicate="tone", value="formal", confidence=0.6, trust="user_file"),
                       [ProvenanceIn(parent_type="chunk", parent_id=cid, quote="short formal replies")])
    mem.edit_fact(r.id, "casual")
    reader = MemoryReader(mem.db_path, keys)
    row = reader.facts(reader.compartment_id("work"), predicate="tone")[0]
    assert row["value"] == "casual" and row["pinned"] == 1
    assert any(p["parent_type"] == "user_edit" for p in reader.provenance_of("fact", r.id))


def test_editing_an_item_invalidates_facts_derived_only_from_old_text(mem, keys):
    first = mem.write_item(CanonicalItem(source="mail", native_id="edit1", compartment="work", trust="user_file", chunks=[ChunkIn(text="Renewal date is 1 March.")]))
    fact = mem.write_fact(FactIn(compartment="work", kind="attribute", subject="Contract", predicate="renewal", value="1 March", confidence=0.9, trust="user_file"),
                          [ProvenanceIn(parent_type="chunk", parent_id=first.chunk_ids[0], quote="Renewal date is 1 March")])
    again = mem.write_item(CanonicalItem(source="mail", native_id="edit1", compartment="work", trust="user_file", chunks=[ChunkIn(text="Renewal date moved to 1 April.")]))
    assert again.action == "replaced"
    reader = MemoryReader(mem.db_path, keys)
    assert reader.provenance_of("fact", fact.id) == []
    assert reader.count("facts") == 0, "a fact whose only evidence was replaced must be removed"
    same = mem.write_item(CanonicalItem(source="mail", native_id="edit1", compartment="work", trust="user_file", chunks=[ChunkIn(text="Renewal date moved to 1 April.")]))
    assert same.action == "unchanged"


# ----------------------------------------------------------------- SEC-15 and retrieval
async def test_SEC_15_compartment_leak(mem, keys, clock):
    mem.write_item(CanonicalItem(source="slack", native_id="s1", compartment="work", trust="received", chunks=[ChunkIn(text="CANARY-WORK-SECRET launch codename is Bifrost")]))
    mem.write_item(CanonicalItem(source="notes", native_id="p1", compartment="personal", trust="user_file", chunks=[ChunkIn(text="grandmother birthday dinner on friday")]))
    gw = FakeGateway()
    reader, index_w = await index_world(mem, keys, gw, "work")
    _, index_p = await index_world(mem, keys, gw, "personal")
    index_all = VectorIndex()
    index_all._ids, index_all._mat = {**index_w._ids, **index_p._ids}, {**index_w._mat, **index_p._mat}  # noqa: SLF001
    res = await retrieve("launch codename Bifrost", compartment="personal", reader=reader, index=index_all, gateway=gw, now_ms=clock.now_ms(), deadline=deadline())
    assert all("CANARY-WORK-SECRET" not in h.text for h in res.hits)
    res_w = await retrieve("launch codename Bifrost", compartment="work", reader=reader, index=index_all, gateway=gw, now_ms=clock.now_ms(), deadline=deadline())
    assert any("Bifrost" in h.text for h in res_w.hits)


async def test_hybrid_retrieval_finds_exact_ids_and_meaning(mem, keys, clock):
    texts = ["Invoice INV-20931 is overdue by twelve days", "The quarterly offsite is planned for the lake house", "Vendor contract renewal date is the first of March",
             "Priya prefers morning meetings", "Grocery list: oats, bananas, coffee"]
    for i, t in enumerate(texts):
        mem.write_item(CanonicalItem(source="mail", native_id=f"h{i}", compartment="work", trust="user_file", title=f"doc {i}", chunks=[ChunkIn(text=t)]))
    gw = FakeGateway()
    reader, index = await index_world(mem, keys, gw)
    exact = await retrieve("INV-20931", compartment="work", reader=reader, index=index, gateway=gw, now_ms=clock.now_ms(), deadline=deadline())
    assert exact.hits and "INV-20931" in exact.hits[0].text
    sem = await retrieve("when does the vendor contract renew", compartment="work", reader=reader, index=index, gateway=gw, now_ms=clock.now_ms(), deadline=deadline())
    assert sem.hits and "renewal" in sem.hits[0].text
    assert sem.hits[0].citation.startswith("mail:")
    assert sem.timings_ms["total"] < 800


async def test_retrieval_respects_token_budget_and_dedupes(mem, keys, clock):
    for i in range(6):
        mem.write_item(CanonicalItem(source="mail", native_id=f"d{i}", compartment="work", trust="user_file", chunks=[ChunkIn(text="vendor contract renewal " + "x" * 300)]))
    gw = FakeGateway()
    reader, index = await index_world(mem, keys, gw)
    res = await retrieve("vendor contract renewal", compartment="work", reader=reader, index=index, gateway=gw, now_ms=clock.now_ms(), deadline=deadline(), token_budget=250)
    assert len(res.hits) == 1, "identical text is deduplicated and the budget caps the pack"
