"""Hybrid retrieval (FR1.1, FR1.2, NFR2.1): vector + BM25 + time range, fused by reciprocal rank,
rescored by recency, importance, confidence and trust, packed into a token budget with citations.

Time expressions are resolved by code. Compartment is a partition and a query condition, not a post-filter.
Access counts are updated by the caller AFTER the response, off the latency-critical path.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any

from ragnarok.core.ids import sha256_hex
from ragnarok.llm_gateway.client import Gateway, Priority
from ragnarok.memory.reader import MemoryReader
from ragnarok.router.analysis import parse_time_range
from ragnarok.store.vectors import VectorIndex

RRF_K = 60
TRUST_WEIGHT = {"user_direct": 1.0, "user_file": 0.98, "agent": 0.95, "received": 0.9, "web": 0.85}
DAY_MS = 86_400_000


@dataclass
class Hit:
    kind: str  # chunk | event | fact
    id: str
    text: str
    score: float
    trust: str
    ts: int | None
    citation: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Retrieval:
    hits: list[Hit]
    time_range: tuple[int, int] | None
    timings_ms: dict[str, float]

    @property
    def fact_ids(self) -> list[str]:
        return [h.id for h in self.hits if h.kind == "fact"]


def approx_tokens(text: str) -> int:
    return len(text) // 3 + 1


def _recency(ts: int | None, now_ms: int) -> float:
    if ts is None:
        return 0.85
    age_days = max(0.0, (now_ms - ts) / DAY_MS)
    return 0.7 + 0.3 * math.exp(-age_days / 90.0)


def _hydrate(reader: MemoryReader, comp_id: str, kind: str, oid: str) -> dict[str, Any] | None:
    r = reader._r  # noqa: SLF001 - same package family; retrieval owns this read shape
    if kind == "chunk":
        row = r.execute(
            "SELECT c.id AS id, c.text AS text, c.page AS page, i.trust AS trust, i.title AS title, i.source AS source, i.deep_link AS deep_link, i.occurred_at AS ts"
            " FROM chunks c JOIN items i ON i.id=c.item_id WHERE c.id=? AND i.compartment_id=? AND i.status<>'archived'", (oid, comp_id)).fetchone()
    elif kind == "event":
        row = r.execute("SELECT id, content AS text, ts, trust, kind FROM events WHERE id=? AND compartment_id=?", (oid, comp_id)).fetchone()
    else:
        row = r.execute(
            "SELECT id, subject, predicate, value, confidence, importance, trust, learned_at AS ts, pinned FROM facts"
            " WHERE id=? AND compartment_id=? AND state='active' AND valid_to IS NULL", (oid, comp_id)).fetchone()
    return dict(row) if row else None


def _to_hit(kind: str, row: dict[str, Any], score: float) -> Hit:
    if kind == "fact":
        text = f"{row['subject']} {row['predicate']}: {row['value']}"
        cite = f"fact:{row['id'][:8]}"
        meta = {"confidence": row["confidence"], "importance": row["importance"], "pinned": row.get("pinned", 0)}
    elif kind == "chunk":
        text = row["text"]
        cite = f"{row.get('source', '?')}:{(row.get('title') or '')[:40]}" + (f" p.{row['page']}" if row.get("page") else "")
        meta = {"deep_link": row.get("deep_link"), "item": row.get("title")}
    else:
        text, cite, meta = row["text"], f"event:{row['id'][:8]}", {"kind": row.get("kind")}
    return Hit(kind, row["id"], text, score, row["trust"], row.get("ts"), cite, meta)


async def retrieve(
    query: str,
    *,
    compartment: str,
    reader: MemoryReader,
    index: VectorIndex,
    gateway: Gateway,
    now_ms: int,
    deadline: float,
    k: int = 8,
    token_budget: int = 1200,
    priority: Priority = Priority.INTERACTIVE,
) -> Retrieval:
    comp_id = reader.compartment_id(compartment)
    if comp_id is None:
        return Retrieval([], None, {})
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    time_range = parse_time_range(query, now_ms)

    def keyword_block() -> dict[str, list[dict[str, Any]]]:
        out = {
            "chunk": reader.fts_chunks(query, comp_id, 25),
            "event": reader.fts_events(query, comp_id, 25),
            "fact": reader.fts_facts(query, comp_id, 25),
        }
        out["range"] = reader.events_in_range(comp_id, *time_range, limit=25) if time_range else []
        return out

    emb_task = gateway.embed([query], deadline=deadline, priority=priority)
    qvecs, kw = await asyncio.gather(emb_task, asyncio.to_thread(keyword_block))
    timings["embed_and_keyword"] = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    vec_hits = index.search(compartment, qvecs[0], k=30)

    ranked: dict[tuple[str, str], float] = {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}

    def add_list(items: list[tuple[str, str]], weight: float = 1.0) -> None:
        for rank, key in enumerate(items):
            ranked[key] = ranked.get(key, 0.0) + weight / (RRF_K + rank + 1)

    for kind in ("chunk", "event", "fact"):
        add_list([(kind, r["id"]) for r in kw[kind]])
        for r in kw[kind]:
            rows[(kind, r["id"])] = r
    if kw["range"]:
        add_list([("event", r["id"]) for r in kw["range"]], weight=1.5)
        for r in kw["range"]:
            rows.setdefault(("event", r["id"]), r)
    add_list([key for key, _ in vec_hits])

    hits: list[Hit] = []
    for key, base in sorted(ranked.items(), key=lambda kv: -kv[1])[: k * 3]:
        kind, oid = key
        row = rows.get(key)
        if row is None or "trust" not in row or (kind == "fact" and "subject" not in row):
            row = _hydrate(reader, comp_id, kind, oid)
        elif kind == "fact" and row.get("state") not in (None, "active"):
            row = None
        if row is None:
            continue
        hit = _to_hit(kind, row, 0.0)
        imp = hit.meta.get("importance", 0.5) if kind == "fact" else 0.5
        conf = hit.meta.get("confidence", 1.0) if kind == "fact" else 1.0
        hit.score = base * _recency(hit.ts, now_ms) * (0.6 + 0.4 * imp) * conf * TRUST_WEIGHT.get(hit.trust, 0.85)
        if time_range and hit.ts and time_range[0] <= hit.ts < time_range[1]:
            hit.score *= 1.5
        hits.append(hit)

    hits.sort(key=lambda h: -h.score)
    packed: list[Hit] = []
    seen: set[str] = set()
    used = 0
    for h in hits:
        fp = sha256_hex(" ".join(h.text.lower().split()))[:16]
        if fp in seen:
            continue
        cost = approx_tokens(h.text)
        if used + cost > token_budget and packed:
            continue
        seen.add(fp)
        packed.append(h)
        used += cost
        if len(packed) >= k:
            break
    timings["fuse_and_pack"] = (time.perf_counter() - t1) * 1000
    timings["total"] = (time.perf_counter() - t0) * 1000
    return Retrieval(packed, time_range, timings)
