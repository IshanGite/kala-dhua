"""Memory Service: the ONLY writer of memory SQL (ARCHITECTURE D-02, CLAUDE.md prohibitions).

Guarantees implemented here:
* one writable connection, every change inside a committed transaction (NFR3.2);
* hash-chained, append-only episodic log with a per-actor rate limit (SEC-08, poisoning burst defense);
* facts must be grounded: every provenance quote has to exist in the cited source text, checked by code;
* contradictions create history (valid_to is closed), they never overwrite; low-trust conflicts wait for the user;
* high-impact facts from untrusted sources are quarantined (rule R-MEM-01).
"""

from __future__ import annotations

import re
import threading
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from ragnarok.core.clock import Clock, get_clock
from ragnarok.core.errors import PolicyDenied, StoreError
from ragnarok.core.failpoints import failpoint
from ragnarok.core.ids import new_id, sha256_hex
from ragnarok.memory.models import (
    LOW_TRUST,
    TRUST_RANK,
    CanonicalItem,
    EventIn,
    FactIn,
    FactResult,
    ItemResult,
    ProvenanceIn,
)
from ragnarok.store.crypto import KeyHierarchy
from ragnarok.store.db import open_writer, transaction
from ragnarok.store.migrate import migrate

GENESIS = "0" * 64
HIGH_IMPACT_WORDS = (
    "bank", "iban", "account_number", "payment", "password", "passcode", "api_key", "token", "secret",
    "email", "phone", "address", "pan", "aadhaar", "ssn", "routing", "swift", "upi", "card",
)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
EVENT_LIMIT = 240  # events per actor per minute before the actor is paused


class UngroundedFact(PolicyDenied):
    code = "E_UNGROUNDED_FACT"


class RateLimited(PolicyDenied):
    code = "E_RATE_LIMIT"
    retryable = True


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def event_hash(prev: str, seq: int, ts: int, comp_id: str, kind: str, actor: str, content: str, trust: str) -> str:
    return sha256_hex("\x1f".join([prev, str(seq), str(ts), comp_id, kind, actor, content, trust]))


def is_high_impact(predicate: str, value: str) -> bool:
    p = predicate.lower()
    return any(w in p for w in HIGH_IMPACT_WORDS) or bool(_IBAN.search(value))


def vec_to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


class MemoryService:
    def __init__(self, db_path: Path, keys: KeyHierarchy, clock: Clock | None = None) -> None:
        self.db_path, self.keys = db_path, keys
        self._clock = clock
        migrate(db_path, keys)
        self._w = open_writer(db_path, keys)
        self._lock = threading.RLock()
        self._comp: dict[str, str] = {}
        self._limits: dict[str, deque[int]] = {}

    def close(self) -> None:
        with self._lock:
            self._w.close()

    def _now(self) -> int:
        return (self._clock or get_clock()).now_ms()

    def with_tx(self, fn: Any) -> Any:
        """Run fn(conn) inside the writer lock and one committed transaction. For helpers that live under memory/."""
        with self._lock, transaction(self._w):
            return fn(self._w)

    # ------------------------------------------------------------------ compartments
    def ensure_compartment(self, name: str) -> str:
        with self._lock:
            if name in self._comp:
                return self._comp[name]
            row = self._w.execute("SELECT id FROM compartments WHERE name=?", (name,)).fetchone()
            if row is None:
                cid = new_id()
                with transaction(self._w):
                    self._w.execute("INSERT INTO compartments(id,name,created_at) VALUES (?,?,?)", (cid, name, self._now()))
            else:
                cid = row[0]
            self._comp[name] = cid
            return cid

    # ------------------------------------------------------------------ episodic memory
    def _check_rate(self, actor: str) -> None:
        now = self._now()
        q = self._limits.setdefault(actor, deque())
        while q and now - q[0] > 60_000:
            q.popleft()
        if len(q) >= EVENT_LIMIT:
            raise RateLimited(f"actor {actor} exceeded {EVENT_LIMIT} events per minute")
        q.append(now)

    def write_event(self, ev: EventIn) -> str:
        with self._lock:
            self._check_rate(ev.actor)
            comp = self.ensure_compartment(ev.compartment)
            ts = ev.ts if ev.ts is not None else self._now()
            with transaction(self._w):
                last = self._w.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
                seq = (last[0] + 1) if last else 1
                prev = last[1] if last else GENESIS
                h = event_hash(prev, seq, ts, comp, ev.kind, ev.actor, ev.content, ev.trust)
                eid = new_id()
                self._w.execute(
                    "INSERT INTO events(id,seq,compartment_id,ts,kind,actor,task_id,content,trust,prev_hash,hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, seq, comp, ts, ev.kind, ev.actor, ev.task_id, ev.content, ev.trust, prev, h),
                )
            failpoint("after_event_committed")
            return eid

    # ------------------------------------------------------------------ items and chunks
    def write_item(self, item: CanonicalItem) -> ItemResult:
        with self._lock:
            comp = self.ensure_compartment(item.compartment)
            chash = item.content_hash or sha256_hex("\x1e".join([item.title or "", *[c.text for c in item.chunks]]))
            now = self._now()
            existing = self._w.execute("SELECT id, content_hash FROM items WHERE source=? AND native_id=?", (item.source, item.native_id)).fetchone()
            with transaction(self._w):
                if existing and existing[1] == chash:
                    return ItemResult(item_id=existing[0], action="unchanged")
                action = "inserted"
                if existing:
                    action = "replaced"
                    item_id = existing[0]
                    old = [r[0] for r in self._w.execute("SELECT id FROM chunks WHERE item_id=?", (item_id,))]
                    self._purge_chunks(old)
                    self._w.execute(
                        "UPDATE items SET content_hash=?, title=?, trust=?, status='raw', kind=?, visibility=?, thread_id=?, deep_link=?,"
                        " occurred_at=?, ingested_at=?, flags_json=? WHERE id=?",
                        (chash, item.title, item.trust, item.kind, item.visibility, item.thread_id, item.deep_link, item.occurred_at, now, _json(item.flags), item_id),
                    )
                else:
                    item_id = new_id()
                    self._w.execute(
                        "INSERT INTO items(id,compartment_id,source,native_id,content_hash,kind,title,trust,status,visibility,thread_id,deep_link,occurred_at,ingested_at,flags_json)"
                        " VALUES (?,?,?,?,?,?,?,?,'raw',?,?,?,?,?,?)",
                        (item_id, comp, item.source, item.native_id, chash, item.kind, item.title, item.trust, item.visibility, item.thread_id, item.deep_link, item.occurred_at, now, _json(item.flags)),
                    )
                chunk_ids: list[str] = []
                for i, c in enumerate(item.chunks):
                    cid = new_id()
                    chunk_ids.append(cid)
                    self._w.execute(
                        "INSERT INTO chunks(id,item_id,ordinal,text,heading_path,page,char_start,char_end) VALUES (?,?,?,?,?,?,?,?)",
                        (cid, item_id, i, c.text, c.heading_path, c.page, c.char_start, c.char_end),
                    )
                    self._w.execute("INSERT INTO provenance(child_type,child_id,parent_type,parent_id,relation,quote) VALUES ('chunk',?,'item',?,'part_of',NULL)", (cid, item_id))
                self._w.execute("UPDATE items SET status='processed' WHERE id=?", (item_id,))
            return ItemResult(item_id=item_id, action=action, chunk_ids=chunk_ids)  # type: ignore[arg-type]

    def put_embedding(self, owner_type: str, owner_id: str, compartment: str, model_id: str, vec: np.ndarray) -> None:
        with self._lock:
            comp = self.ensure_compartment(compartment)
            with transaction(self._w):
                self._w.execute(
                    "INSERT OR REPLACE INTO embeddings(owner_type,owner_id,compartment_id,model_id,dim,vec) VALUES (?,?,?,?,?,?)",
                    (owner_type, owner_id, comp, model_id, int(vec.shape[0]), vec_to_blob(vec)),
                )

    # ------------------------------------------------------------------ semantic memory
    def _parent_text(self, p: ProvenanceIn) -> str | None:
        if p.parent_type == "chunk":
            r = self._w.execute("SELECT text FROM chunks WHERE id=?", (p.parent_id,)).fetchone()
        elif p.parent_type == "event":
            r = self._w.execute("SELECT content FROM events WHERE id=?", (p.parent_id,)).fetchone()
        elif p.parent_type == "item":
            rows = self._w.execute("SELECT text FROM chunks WHERE item_id=? ORDER BY ordinal", (p.parent_id,)).fetchall()
            return "\n".join(x[0] for x in rows) if rows else None
        else:
            return None
        return r[0] if r else None

    def _check_grounded(self, provenance: list[ProvenanceIn]) -> None:
        if not provenance:
            raise UngroundedFact("a fact needs at least one provenance record")
        for p in provenance:
            if p.parent_type == "user_edit":
                continue
            text = self._parent_text(p)
            if text is None:
                raise UngroundedFact(f"provenance parent {p.parent_type} not found")
            if not p.quote or _norm(p.quote) not in _norm(text):
                raise UngroundedFact("quoted evidence does not appear in the cited source")

    def write_fact(self, fact: FactIn, provenance: list[ProvenanceIn]) -> FactResult:
        with self._lock:
            comp = self.ensure_compartment(fact.compartment)
            now = self._now()
            self._check_grounded(provenance)
            subj, pred = fact.subject.strip().lower(), fact.predicate.strip().lower()
            with transaction(self._w):
                rows = self._w.execute(
                    "SELECT id, value, trust FROM facts WHERE compartment_id=? AND lower(subject)=? AND lower(predicate)=?"
                    " AND state IN ('active','needs_confirmation') AND valid_to IS NULL",
                    (comp, subj, pred),
                ).fetchall()
                same = [r for r in rows if _norm(r[1]) == _norm(fact.value)]
                if same:
                    fid = same[0][0]
                    self._add_provenance("fact", fid, provenance)
                    self._w.execute("UPDATE facts SET confidence=max(confidence, ?) WHERE id=?", (fact.confidence, fid))
                    return FactResult(id=fid, state="active", action="duplicate")
                conflicts = [r for r in rows if _norm(r[1]) != _norm(fact.value)]
                state, action, closed = "active", "inserted", []
                if fact.trust in LOW_TRUST and is_high_impact(fact.predicate, fact.value):
                    state, action = "quarantined", "quarantined"
                elif conflicts and fact.trust in LOW_TRUST and max(TRUST_RANK[c[2]] for c in conflicts) > TRUST_RANK[fact.trust]:
                    state, action = "needs_confirmation", "needs_confirmation"
                elif conflicts:
                    closed = [c[0] for c in conflicts]
                    self._w.execute(f"UPDATE facts SET valid_to=? WHERE id IN ({','.join('?' * len(closed))})", (now, *closed))
                    action = "superseded_old"
                fid = new_id()
                self._w.execute(
                    "INSERT INTO facts(id,compartment_id,kind,subject,predicate,value,confidence,importance,trust,valid_from,valid_to,learned_at,state)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, comp, fact.kind, fact.subject.strip(), fact.predicate.strip(), fact.value, fact.confidence, fact.importance, fact.trust, fact.valid_from or now, fact.valid_to, now, state),
                )
                self._add_provenance("fact", fid, provenance)
            return FactResult(id=fid, state=state, action=action, closed_ids=closed)  # type: ignore[arg-type]

    def _add_provenance(self, child_type: str, child_id: str, provenance: list[ProvenanceIn]) -> None:
        for p in provenance:
            self._w.execute(
                "INSERT OR IGNORE INTO provenance(child_type,child_id,parent_type,parent_id,relation,quote) VALUES (?,?,?,?,?,?)",
                (child_type, child_id, p.parent_type, p.parent_id, p.relation, p.quote),
            )

    def confirm_fact(self, fact_id: str, accept: bool) -> None:
        """The user resolves a quarantined or needs_confirmation fact."""
        with self._lock, transaction(self._w):
            row = self._w.execute("SELECT subject,predicate,compartment_id,state FROM facts WHERE id=?", (fact_id,)).fetchone()
            if row is None or row[3] not in ("quarantined", "needs_confirmation"):
                raise StoreError("fact is not awaiting confirmation")
            if accept:
                now = self._now()
                self._w.execute(
                    "UPDATE facts SET valid_to=? WHERE compartment_id=? AND lower(subject)=lower(?) AND lower(predicate)=lower(?) AND state='active' AND valid_to IS NULL AND id<>?",
                    (now, row[2], row[0], row[1], fact_id),
                )
                self._w.execute("UPDATE facts SET state='active', pinned=1 WHERE id=?", (fact_id,))
            else:
                self._purge_facts([fact_id])

    def edit_fact(self, fact_id: str, new_value: str) -> None:
        """A user edit: the value changes, the fact is pinned (forgetting never overrides the user), and provenance says so."""
        with self._lock, transaction(self._w):
            if self._w.execute("SELECT 1 FROM facts WHERE id=?", (fact_id,)).fetchone() is None:
                raise StoreError("no such fact")
            self._w.execute("UPDATE facts SET value=?, pinned=1, trust='user_direct', confidence=1.0 WHERE id=?", (new_value, fact_id))
            self._add_provenance("fact", fact_id, [ProvenanceIn(parent_type="user_edit", parent_id="ui", relation="edited_by")])
            self._w.execute(
                "INSERT INTO change_log(id,ts,actor,op,counts_json,reason_code) VALUES (?,?,?,?,?,?)",
                (new_id(), self._now(), "user", "edit_fact", '{"facts":1}', "USER_EDIT"),
            )

    def bump_access(self, fact_ids: list[str]) -> None:
        """Called after a response is sent, off the latency-critical path."""
        if not fact_ids:
            return
        with self._lock, transaction(self._w):
            now = self._now()
            self._w.executemany("UPDATE facts SET access_count=access_count+1, last_accessed=? WHERE id=?", [(now, f) for f in fact_ids])

    # ------------------------------------------------------------------ cascade helpers (full delete lives in memory/delete.py)
    def _purge_facts(self, fact_ids: list[str]) -> int:
        if not fact_ids:
            return 0
        q = ",".join("?" * len(fact_ids))
        self._w.execute(f"DELETE FROM embeddings WHERE owner_type='fact' AND owner_id IN ({q})", fact_ids)
        self._w.execute(f"DELETE FROM provenance WHERE child_type='fact' AND child_id IN ({q})", fact_ids)
        self._w.execute(f"DELETE FROM provenance WHERE parent_type='fact' AND parent_id IN ({q})", fact_ids)
        self._w.execute(f"DELETE FROM facts WHERE id IN ({q})", fact_ids)
        return len(fact_ids)

    def _purge_chunks(self, chunk_ids: list[str]) -> dict[str, int]:
        """Remove chunks and everything derived only from them. Facts with other surviving evidence keep it and are re-flagged."""
        counts = {"facts": 0, "facts_reflagged": 0, "saga": 0, "embeddings": 0, "chunks": 0}
        if not chunk_ids:
            return counts
        q = ",".join("?" * len(chunk_ids))
        derived = [r[0] for r in self._w.execute(f"SELECT DISTINCT child_id FROM provenance WHERE child_type='fact' AND parent_type='chunk' AND parent_id IN ({q})", chunk_ids)]
        orphan, kept = [], []
        for fid in derived:
            remaining = self._w.execute(
                f"SELECT count(*) FROM provenance WHERE child_type='fact' AND child_id=? AND NOT (parent_type='chunk' AND parent_id IN ({q}))",
                [fid, *chunk_ids],
            ).fetchone()[0]
            (orphan if remaining == 0 else kept).append(fid)
        counts["facts"] = self._purge_facts(orphan)
        if kept:
            self._w.execute(f"UPDATE facts SET state='needs_confirmation' WHERE id IN ({','.join('?' * len(kept))}) AND pinned=0", kept)
            counts["facts_reflagged"] = len(kept)
        sagas = [r[0] for r in self._w.execute(f"SELECT DISTINCT child_id FROM provenance WHERE child_type='saga' AND parent_type='chunk' AND parent_id IN ({q})", chunk_ids)]
        if sagas:
            sq = ",".join("?" * len(sagas))
            self._w.execute(f"DELETE FROM provenance WHERE child_type='saga' AND child_id IN ({sq})", sagas)
            self._w.execute(f"DELETE FROM saga WHERE id IN ({sq})", sagas)
            counts["saga"] = len(sagas)
        cur = self._w.execute(f"DELETE FROM embeddings WHERE owner_type='chunk' AND owner_id IN ({q})", chunk_ids)
        counts["embeddings"] = cur.rowcount
        self._w.execute(f"DELETE FROM provenance WHERE (child_type='chunk' AND child_id IN ({q})) OR (parent_type='chunk' AND parent_id IN ({q}))", [*chunk_ids, *chunk_ids])
        cur = self._w.execute(f"DELETE FROM chunks WHERE id IN ({q})", chunk_ids)
        counts["chunks"] = cur.rowcount
        return counts


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))
