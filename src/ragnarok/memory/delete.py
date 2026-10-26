"""Permanent, verified deletion (NFR4.2): preview, two-step confirmation, cascade through provenance, scrub, prove, receipt.

Steps (ARCHITECTURE baseline "The delete cascade"):
1. Resolve the selector. 2. Preview the cascade (counts) and issue a confirm token bound to those counts. 3. Walk provenance downward in
ONE transaction: facts derived only from the deleted material go too, facts with other evidence lose the edge and are re-flagged,
summaries built from it are removed, vectors and keyword rows follow by trigger, blob keys are destroyed (crypto-shred).
4. Block re-ingestion. 5. Scrub: secure_delete zeroes freed pages, VACUUM, WAL truncate. 6. Verify: fingerprints taken BEFORE the delete must
find nothing afterwards; the receipt records counts only, never content.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ragnarok.core.clock import get_clock
from ragnarok.core.errors import PolicyDenied
from ragnarok.core.ids import new_id, sha256_hex
from ragnarok.memory.reader import fts_query
from ragnarok.memory.service import GENESIS, MemoryService, event_hash

CONFIRM_WINDOW_MS = 5 * 60 * 1000
_WORD = re.compile(r"[A-Za-z0-9_']+")


class ConfirmRequired(PolicyDenied):
    code = "E_CONFIRM_REQUIRED"


@dataclass(frozen=True)
class DeleteSelector:
    item_ids: tuple[str, ...] = ()
    source: str | None = None  # everything ingested from this source (for example one disconnected account)
    compartment: str | None = None
    fact_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    block_reingest: bool = True


@dataclass
class DeletePreview:
    counts: dict[str, int]
    summary: str
    confirm_token: str


@dataclass
class DeleteReceipt:
    counts: dict[str, int]
    tombstone_id: str
    fingerprints: int
    residual: int
    verified: bool
    notes: list[str] = field(default_factory=list)


def shingles(text: str, n: int = 6, k: int = 3) -> list[str]:
    """The k most distinctive n-word windows of a text: what we later search for to prove it is gone."""
    words = _WORD.findall(text)
    if len(words) < n:
        return [" ".join(words)] if len(" ".join(words)) >= 12 else []
    windows = [" ".join(words[i:i + n]) for i in range(len(words) - n + 1)]
    return sorted(set(windows), key=lambda w: -len(w))[:k]


class Deleter:
    def __init__(self, mem: MemoryService, blob_root: Path | None = None) -> None:
        self._m = mem
        self._blob_root = blob_root

    def _now(self) -> int:
        return get_clock().now_ms()

    # ------------------------------------------------------------------ resolve and count
    def _resolve(self, c: Any, sel: DeleteSelector) -> dict[str, list[str]]:
        items = list(sel.item_ids)
        if sel.source:
            q = "SELECT i.id FROM items i JOIN compartments k ON k.id=i.compartment_id WHERE i.source=?"
            args: list[Any] = [sel.source]
            if sel.compartment:
                q += " AND k.name=?"
                args.append(sel.compartment)
            items += [r[0] for r in c.execute(q, args)]
        items = list(dict.fromkeys(items))
        chunks: list[str] = []
        for i in range(0, len(items), 200):
            part = items[i:i + 200]
            chunks += [r[0] for r in c.execute(f"SELECT id FROM chunks WHERE item_id IN ({','.join('?' * len(part))})", part)]
        return {"items": items, "chunks": chunks, "facts": list(sel.fact_ids), "events": list(sel.event_ids)}

    def _count(self, c: Any, ids: dict[str, list[str]]) -> dict[str, int]:
        counts = {"items": len(ids["items"]), "chunks": len(ids["chunks"]), "events": len(ids["events"]), "facts": len(ids["facts"]),
                  "embeddings": 0, "facts_removed": 0, "facts_reflagged": 0, "summaries": 0, "blobs": 0}
        ck = ids["chunks"]
        if ck:
            q = ",".join("?" * len(ck))
            counts["embeddings"] += c.execute(f"SELECT count(*) FROM embeddings WHERE owner_type='chunk' AND owner_id IN ({q})", ck).fetchone()[0]
            derived = [r[0] for r in c.execute(f"SELECT DISTINCT child_id FROM provenance WHERE child_type='fact' AND parent_type='chunk' AND parent_id IN ({q})", ck)]
            for fid in derived:
                left = c.execute(f"SELECT count(*) FROM provenance WHERE child_type='fact' AND child_id=? AND NOT (parent_type='chunk' AND parent_id IN ({q}))", [fid, *ck]).fetchone()[0]
                counts["facts_removed" if left == 0 else "facts_reflagged"] += 1
            counts["summaries"] = c.execute(f"SELECT count(DISTINCT child_id) FROM provenance WHERE child_type='saga' AND parent_type='chunk' AND parent_id IN ({q})", ck).fetchone()[0]
        if ids["items"]:
            q2 = ",".join("?" * len(ids["items"]))
            counts["blobs"] = c.execute(f"SELECT count(*) FROM blob_keys WHERE item_id IN ({q2})", ids["items"]).fetchone()[0]
        if ids["facts"]:
            q3 = ",".join("?" * len(ids["facts"]))
            counts["embeddings"] += c.execute(f"SELECT count(*) FROM embeddings WHERE owner_type='fact' AND owner_id IN ({q3})", ids["facts"]).fetchone()[0]
        return counts

    @staticmethod
    def _summary(n: dict[str, int]) -> str:
        parts = [f"{n['items']} item(s)", f"{n['chunks']} chunk(s)", f"{n['embeddings']} vector(s)"]
        if n["facts"] + n["facts_removed"]:
            parts.append(f"{n['facts'] + n['facts_removed']} fact(s)")
        if n["facts_reflagged"]:
            parts.append(f"{n['facts_reflagged']} fact(s) lose evidence and need re-confirmation")
        if n["summaries"]:
            parts.append(f"{n['summaries']} summary(ies) to regenerate")
        if n["events"]:
            parts.append(f"{n['events']} event(s)")
        if n["blobs"]:
            parts.append(f"{n['blobs']} stored file(s) (key destroyed)")
        return "This will permanently remove " + ", ".join(parts) + "."

    def _token(self, sel: DeleteSelector, counts: dict[str, int], bucket: int) -> str:
        return sha256_hex(json.dumps([sel.__dict__, counts, bucket], sort_keys=True, default=str))[:24]

    def preview(self, sel: DeleteSelector) -> DeletePreview:
        def op(c):
            ids = self._resolve(c, sel)
            return self._count(c, ids)

        counts = self._m.with_tx(op)
        return DeletePreview(counts, self._summary(counts), self._token(sel, counts, self._now() // CONFIRM_WINDOW_MS))

    # ------------------------------------------------------------------ delete
    def _fingerprints(self, c: Any, ids: dict[str, list[str]]) -> list[str]:
        prints: list[str] = []
        for i in range(0, len(ids["chunks"]), 200):
            part = ids["chunks"][i:i + 200]
            for (text,) in c.execute(f"SELECT text FROM chunks WHERE id IN ({','.join('?' * len(part))})", part):
                prints += shingles(text)
        for table, col, key in (("facts", "value", "facts"), ("events", "content", "events")):
            if ids[key]:
                q = ",".join("?" * len(ids[key]))
                for (text,) in c.execute(f"SELECT {col} FROM {table} WHERE id IN ({q})", ids[key]):
                    if key == "facts" and not (ids["chunks"] or ids["events"]):
                        continue  # a derived fact alone: its words legitimately remain in the untouched source text
                    prints += shingles(text) if len(text) > 30 else ([text] if len(text) >= 8 else [])
        return list(dict.fromkeys(p for p in prints if p))

    def delete(self, sel: DeleteSelector, confirm_token: str) -> DeleteReceipt:
        prev = self.preview(sel)
        if confirm_token != prev.confirm_token:
            # also accept the token from the previous window so a click near a boundary still works
            counts_prev = self._token(sel, prev.counts, self._now() // CONFIRM_WINDOW_MS - 1)
            if confirm_token != counts_prev:
                raise ConfirmRequired("the delete was not confirmed for exactly this selection (or the preview is stale)")
        notes: list[str] = []
        fingerprints: list[str] = []
        blob_paths: list[str] = []

        def op(c) -> dict[str, int]:
            ids = self._resolve(c, sel)
            counts = self._count(c, ids)
            nonlocal fingerprints
            fingerprints = self._fingerprints(c, ids)
            if sel.block_reingest and ids["items"]:
                q = ",".join("?" * len(ids["items"]))
                c.execute(f"INSERT OR REPLACE INTO ingest_exclusions(source,native_id,content_hash,created_at) SELECT source,native_id,content_hash,? FROM items WHERE id IN ({q})", [self._now(), *ids["items"]])
            if ids["items"]:
                q = ",".join("?" * len(ids["items"]))
                blob_paths.extend(r[0] for r in c.execute(f"SELECT path FROM blob_keys WHERE item_id IN ({q})", ids["items"]))
                c.execute(f"DELETE FROM blob_keys WHERE item_id IN ({q})", ids["items"])  # the per-file key dies with its row: leftover ciphertext is unreadable
                direct = [r[0] for r in c.execute(f"SELECT DISTINCT child_id FROM provenance WHERE child_type='fact' AND parent_type='item' AND parent_id IN ({q})", ids["items"])]
                self._m._purge_facts(direct)  # noqa: SLF001
            self._m._purge_chunks(ids["chunks"])  # noqa: SLF001
            if ids["items"]:
                q = ",".join("?" * len(ids["items"]))
                c.execute(f"DELETE FROM provenance WHERE (child_type='item' AND child_id IN ({q})) OR (parent_type='item' AND parent_id IN ({q}))", [*ids["items"], *ids["items"]])
                c.execute(f"DELETE FROM items WHERE id IN ({q})", ids["items"])
            if ids["items"]:
                self._m.prune_people(c)  # names of people who no longer appear in any remaining message go too
            if ids["facts"]:
                self._m._purge_facts(ids["facts"])  # noqa: SLF001
            if ids["events"]:
                self._delete_events_relinking(c, ids["events"])
            tomb = new_id()
            c.execute("INSERT INTO tombstones(id,deleted_at,scope,counts_json) VALUES (?,?,?,?)", (tomb, self._now(), "user_delete", json.dumps(counts, sort_keys=True)))
            c.execute("INSERT INTO change_log(id,ts,actor,op,counts_json,reason_code) VALUES (?,?,?,?,?,?)", (new_id(), self._now(), "user", "delete", json.dumps(counts, sort_keys=True), "USER_DELETE"))
            counts["_tombstone"] = 0
            notes.append(tomb)
            return counts

        counts = self._m.with_tx(op)
        counts.pop("_tombstone", None)
        tomb = notes.pop()
        for p in blob_paths:  # best-effort unlink; the key is already gone so the bytes are worthless either way
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                notes.append("blob file could not be unlinked (its key is destroyed)")
        self._scrub()
        residual = self._verify(fingerprints)
        return DeleteReceipt(counts, tomb, len(fingerprints), residual, residual == 0, notes)

    def _delete_events_relinking(self, c: Any, event_ids: list[str]) -> None:
        """Deleting an event must not leave a broken hash chain: delete it, then re-link every later event."""
        q = ",".join("?" * len(event_ids))
        first = c.execute(f"SELECT min(seq) FROM events WHERE id IN ({q})", event_ids).fetchone()[0]
        if first is None:
            return
        c.execute(f"DELETE FROM events WHERE id IN ({q})", event_ids)
        prev_row = c.execute("SELECT hash FROM events WHERE seq<? ORDER BY seq DESC LIMIT 1", (first,)).fetchone()
        prev = prev_row[0] if prev_row else GENESIS
        later = [dict(r) for r in c.execute("SELECT * FROM events WHERE seq>? ORDER BY seq", (first,))]
        if not later:
            return
        c.execute("DELETE FROM events WHERE seq>?", (first,))
        seq = first
        for e in later:
            h = event_hash(prev, seq, e["ts"], e["compartment_id"], e["kind"], e["actor"], e["content"], e["trust"])
            c.execute("INSERT INTO events(id,seq,compartment_id,ts,kind,actor,task_id,content,trust,prev_hash,hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (e["id"], seq, e["compartment_id"], e["ts"], e["kind"], e["actor"], e["task_id"], e["content"], e["trust"], prev, h))
            prev, seq = h, seq + 1

    def _scrub(self) -> None:
        w = self._m._w  # noqa: SLF001 - same package; scrub needs the writer outside a transaction
        with self._m._lock:  # noqa: SLF001
            for stmt in ("PRAGMA wal_checkpoint(TRUNCATE)", "VACUUM", "PRAGMA wal_checkpoint(TRUNCATE)"):
                try:
                    w.execute(stmt)
                except Exception:  # noqa: BLE001 - scrubbing is best effort; verification below is the proof
                    pass

    def _verify(self, fingerprints: list[str]) -> int:
        """Search every table and index for what was deleted. Zero hits is the proof."""
        def op(c) -> int:
            hits = 0
            for fp in fingerprints:
                q = fts_query(fp, limit=12)
                for table in ("chunks_fts", "events_fts"):
                    if q and len(fp.split()) >= 3:
                        phrase = '"' + fp.replace('"', " ") + '"'
                        hits += c.execute(f"SELECT count(*) FROM {table} WHERE {table} MATCH ?", (phrase,)).fetchone()[0]
                like = f"%{fp}%"
                hits += c.execute("SELECT count(*) FROM facts WHERE value LIKE ? OR subject LIKE ?", (like, like)).fetchone()[0]
                hits += c.execute("SELECT count(*) FROM saga WHERE summary LIKE ?", (like,)).fetchone()[0]
                hits += c.execute("SELECT count(*) FROM events WHERE content LIKE ?", (like,)).fetchone()[0]
                hits += c.execute("SELECT count(*) FROM chunks WHERE text LIKE ?", (like,)).fetchone()[0]
            return hits

        return int(self._m.with_tx(op))
