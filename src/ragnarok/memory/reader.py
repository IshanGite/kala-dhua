"""Read-only access to memory (PRAGMA query_only). Retrieval and everything that is not the Memory Service uses this."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from ragnarok.memory.service import GENESIS, blob_to_vec, event_hash
from ragnarok.store.crypto import KeyHierarchy
from ragnarok.store.db import open_reader

_TOKEN = re.compile(r"[A-Za-z0-9_]{2,}")
_STOP = {"the", "and", "for", "what", "when", "did", "does", "with", "that", "this", "from", "have", "about", "was", "were", "are", "you", "your", "can", "how", "who", "which"}


def fts_query(text: str, limit: int = 12) -> str | None:
    """Make free text safe for FTS5: quoted tokens joined with OR. Returns None if nothing searchable remains."""
    toks = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP][:limit]
    return " OR ".join(f'"{t}"' for t in toks) if toks else None


class MemoryReader:
    def __init__(self, db_path: Path, keys: KeyHierarchy) -> None:
        self._r = open_reader(db_path, keys)

    def close(self) -> None:
        self._r.close()

    def compartment_id(self, name: str) -> str | None:
        r = self._r.execute("SELECT id FROM compartments WHERE name=?", (name,)).fetchone()
        return r[0] if r else None

    # ---------------------------------------------------------------- keyword search (BM25)
    def fts_chunks(self, query: str, comp_id: str, k: int = 20) -> list[dict[str, Any]]:
        q = fts_query(query)
        if not q:
            return []
        rows = self._r.execute(
            "SELECT c.id AS id, c.text AS text, c.item_id AS item_id, c.page AS page, i.trust AS trust, i.title AS title,"
            " i.source AS source, i.deep_link AS deep_link, i.occurred_at AS ts, bm25(chunks_fts) AS s"
            " FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid JOIN items i ON i.id = c.item_id"
            " WHERE chunks_fts MATCH ? AND i.compartment_id=? AND i.status<>'archived' ORDER BY s LIMIT ?",
            (q, comp_id, k),
        ).fetchall()
        return [dict(r) for r in rows]

    def fts_events(self, query: str, comp_id: str, k: int = 20) -> list[dict[str, Any]]:
        q = fts_query(query)
        if not q:
            return []
        rows = self._r.execute(
            "SELECT e.id AS id, e.content AS text, e.ts AS ts, e.trust AS trust, e.kind AS kind, bm25(events_fts) AS s"
            " FROM events_fts JOIN events e ON e.rowid = events_fts.rowid"
            " WHERE events_fts MATCH ? AND e.compartment_id=? AND e.kind NOT IN ('user_message','assistant_message','clarification','task_result','draft') ORDER BY s LIMIT ?",
            (q, comp_id, k),
        ).fetchall()
        return [dict(r) for r in rows]

    def fts_facts(self, query: str, comp_id: str, k: int = 20) -> list[dict[str, Any]]:
        q = fts_query(query)
        if not q:
            return []
        rows = self._r.execute(
            "SELECT f.id AS id, f.subject AS subject, f.predicate AS predicate, f.value AS value, f.confidence AS confidence,"
            " f.importance AS importance, f.trust AS trust, f.learned_at AS ts, f.pinned AS pinned, bm25(facts_fts) AS s"
            " FROM facts_fts JOIN facts f ON f.rowid = facts_fts.rowid"
            " WHERE facts_fts MATCH ? AND f.compartment_id=? AND f.state='active' AND f.valid_to IS NULL ORDER BY s LIMIT ?",
            (q, comp_id, k),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- episodic
    def events_in_range(self, comp_id: str, start_ms: int, end_ms: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._r.execute(
            "SELECT id, ts, kind, actor, content AS text, trust FROM events WHERE compartment_id=? AND ts>=? AND ts<? ORDER BY ts DESC LIMIT ?",
            (comp_id, start_ms, end_ms, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_events(self, comp_id: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._r.execute(
            "SELECT id, ts, kind, actor, content AS text, trust FROM events WHERE compartment_id=? ORDER BY ts DESC LIMIT ?", (comp_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- semantic
    def facts(self, comp_id: str, *, subject: str | None = None, predicate: str | None = None, as_of: int | None = None, include_closed: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM facts WHERE compartment_id=?"
        args: list[Any] = [comp_id]
        if subject:
            sql += " AND lower(subject)=lower(?)"
            args.append(subject)
        if predicate:
            sql += " AND lower(predicate)=lower(?)"
            args.append(predicate)
        if as_of is not None:  # time travel: what did we believe was true at that moment, using only what we had learned by then
            sql += " AND learned_at<=? AND (valid_from IS NULL OR valid_from<=?) AND (valid_to IS NULL OR valid_to>?)"
            args += [as_of, as_of, as_of]
        elif not include_closed:
            sql += " AND valid_to IS NULL AND state='active'"
        return [dict(r) for r in self._r.execute(sql + " ORDER BY learned_at DESC", args)]

    def facts_by_state(self, comp_id: str, state: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._r.execute("SELECT * FROM facts WHERE compartment_id=? AND state=?", (comp_id, state))]

    def recent_items(self, *, limit: int = 40, source: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        """What has been read, newest first, with a short preview. For the user's own window only; never logged."""
        sql = ("SELECT i.id, i.source, i.title, i.kind, i.trust, i.visibility, coalesce(i.occurred_at, i.ingested_at) AS ts, i.deep_link, "
               "(SELECT text FROM chunks c WHERE c.item_id = i.id ORDER BY ordinal LIMIT 1) AS preview FROM items i")
        where, args = [], []
        if source:
            where.append("i.source = ?")
            args.append(source)
        if query and (q := fts_query(query)):
            where.append("i.id IN (SELECT c.item_id FROM chunks c WHERE c.rowid IN (SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?))")
            args.append(q)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ?"
        return [dict(x) for x in self._r.execute(sql, [*args, max(1, min(limit, 200))])]

    def items_window(self, comp_id: str, since_ms: int, *, until_ms: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        """Everything that happened in a window, with its text and (if embedded) one vector per item: the raw material for the digest. Newest first."""
        import json as _json

        sql = ("SELECT i.id, i.source, i.title, i.kind, i.trust, i.thread_id, i.deep_link, coalesce(i.occurred_at, i.ingested_at) AS ts, i.ingested_at, i.flags_json FROM items i "
               "WHERE i.compartment_id=? AND coalesce(i.occurred_at, i.ingested_at) >= ?" + (" AND coalesce(i.occurred_at, i.ingested_at) < ?" if until_ms else "") + " ORDER BY ts DESC LIMIT ?")
        args: list[Any] = [comp_id, since_ms] + ([until_ms] if until_ms else []) + [limit]
        rows = [dict(x) for x in self._r.execute(sql, args)]
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        texts: dict[str, list[str]] = {i: [] for i in ids}
        vecs: dict[str, list[Any]] = {i: [] for i in ids}
        for k in range(0, len(ids), 200):
            part = ids[k:k + 200]
            q = ",".join("?" * len(part))
            for iid, text in self._r.execute(f"SELECT item_id, text FROM chunks WHERE item_id IN ({q}) ORDER BY item_id, ordinal", part):
                if len(texts[iid]) < 4:
                    texts[iid].append(text)
            for iid, blob in self._r.execute(f"SELECT c.item_id, e.vec FROM chunks c JOIN embeddings e ON e.owner_type='chunk' AND e.owner_id=c.id WHERE c.item_id IN ({q})", part):
                vecs[iid].append(blob_to_vec(blob))
        for r_ in rows:
            fl = _json.loads(r_.pop("flags_json") or "{}")
            r_["author"] = fl.get("author") or fl.get("from") or ""
            r_["text"] = "\n".join(texts[r_["id"]])[:1800]
            v = vecs[r_["id"]]
            r_["vec"] = (np.mean(np.stack(v), axis=0) if v else None)
        return rows

    def items_since(self, ingested_after_ms: int, limit: int = 100) -> list[dict[str, Any]]:
        """Items that arrived after a point in time, oldest first: what the proactive engine looks at."""
        import json as _json

        rows = self._r.execute("SELECT i.id, i.source, i.title, i.kind, i.trust, i.native_id, i.deep_link, i.ingested_at, coalesce(i.occurred_at, i.ingested_at) AS ts, i.flags_json, "
                               "(SELECT text FROM chunks c WHERE c.item_id = i.id ORDER BY ordinal LIMIT 1) AS preview FROM items i WHERE i.ingested_at > ? ORDER BY i.ingested_at LIMIT ?", (ingested_after_ms, limit)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            fl = _json.loads(d.pop("flags_json") or "{}")
            d["author"] = fl.get("author") or fl.get("from") or ""
            d["from_addr"] = fl.get("from") or ""
            out.append(d)
        return out

    def chunk_context(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Who, where and when for retrieved chunks, so an answer can say 'Meera on Discord, Sun 16:54' and not only the text."""
        if not chunk_ids:
            return {}
        import json as _json

        q = ",".join("?" * len(chunk_ids))
        out = {}
        for row in self._r.execute(f"SELECT c.id AS cid, i.source, i.title, coalesce(i.occurred_at, i.ingested_at) AS ts, i.flags_json FROM chunks c JOIN items i ON i.id=c.item_id WHERE c.id IN ({q})", chunk_ids):
            fl = _json.loads(row["flags_json"] or "{}")
            out[row["cid"]] = {"source": row["source"], "title": row["title"], "ts": row["ts"], "author": fl.get("author") or fl.get("from")}
        return out

    def find_mail(self, source: str, who: str) -> dict[str, Any] | None:
        """The most recent message on a mail source whose sender (name or address) matches; carries what a threaded reply needs."""
        row = self._r.execute("SELECT native_id, title, thread_id, flags_json FROM items WHERE source=? AND lower(flags_json) LIKE ? ORDER BY coalesce(occurred_at,0) DESC LIMIT 1", (source, f"%{who.lower()}%")).fetchone()
        if row is None:  # the name may be a signature in the text or the topic in the subject, not the From header
            row = self._r.execute("SELECT i.native_id, i.title, i.thread_id, i.flags_json FROM items i WHERE i.source=? AND (lower(i.title) LIKE ? OR EXISTS "
                                  "(SELECT 1 FROM chunks c WHERE c.item_id=i.id AND lower(c.text) LIKE ?)) ORDER BY coalesce(i.occurred_at,0) DESC LIMIT 1", (source, f"%{who.lower()}%", f"%{who.lower()}%")).fetchone()
        if row is None:
            return None
        import json as _json

        return {"native_id": row["native_id"], "title": row["title"], "thread_id": row["thread_id"], "flags": _json.loads(row["flags_json"] or "{}")}

    def items_between(self, sources: tuple[str, ...], start_ms: int, end_ms: int, limit: int = 60) -> list[dict[str, Any]]:
        """Items whose own time (an event's start, a message's date) falls in the window, soonest first. For agendas."""
        q = ",".join("?" * len(sources))
        rows = self._r.execute(f"SELECT i.source, i.title, i.occurred_at AS ts, i.deep_link, (SELECT text FROM chunks c WHERE c.item_id=i.id ORDER BY ordinal LIMIT 1) AS preview "
                               f"FROM items i WHERE i.source IN ({q}) AND i.occurred_at >= ? AND i.occurred_at < ? ORDER BY i.occurred_at LIMIT ?", (*sources, start_ms, end_ms, limit)).fetchall()
        return [dict(x) for x in rows]

    CHAT_FILLER = {"the", "a", "channel", "chat", "group", "server", "on", "in", "to", "dm", "room", "conversation", "thread", "discord", "slack", "telegram", "gmail", "email"}

    def find_conversation(self, source: str, name: str) -> dict[str, Any] | None:
        """The most recent conversation on a platform whose title matches a name: native_id is 'chat:message' for chat sources.
        'the general channel' matches a title like 'team #general': filler words are dropped and every remaining word must appear in the title."""
        row = self._r.execute("SELECT native_id, title FROM items WHERE source=? AND title LIKE ? ORDER BY coalesce(occurred_at,0) DESC LIMIT 1", (source, f"%{name}%")).fetchone()
        if row is None:
            words = [w for w in re.findall(r"[\w#@.-]+", name.lower()) if w not in self.CHAT_FILLER]
            if words:
                cond = " AND ".join("lower(title) LIKE ?" for _ in words)
                row = self._r.execute(f"SELECT native_id, title FROM items WHERE source=? AND {cond} ORDER BY coalesce(occurred_at,0) DESC LIMIT 1", (source, *[f"%{w}%" for w in words])).fetchone()
        if row is None:
            words = [w for w in re.findall(r"[\w#@.-]+", name.lower()) if w not in self.CHAT_FILLER and len(w) > 2]
            for w in words:  # 'priya on slack general': a conversation named after any one of the words, or one a person of that name wrote in
                row = self._r.execute("SELECT native_id, title FROM items WHERE source=? AND (lower(title) LIKE ? OR lower(flags_json) LIKE ?) ORDER BY coalesce(occurred_at,0) DESC LIMIT 1", (source, f"%{w}%", f"%{w}%")).fetchone()
                if row is not None:
                    break
        return dict(row) if row else None

    def items_by_person(self, comp_id: str, person: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """The latest items sent by, or titled with, a person (name or address), newest first, with text. For 'what did Ravi say'."""
        import json as _json

        like = f"%{person.lower()}%"
        rows = [dict(x) for x in self._r.execute("SELECT i.id, i.source, i.title, i.trust, i.deep_link, coalesce(i.occurred_at, i.ingested_at) AS ts, i.flags_json FROM items i "
                                                 "WHERE i.compartment_id=? AND (lower(i.flags_json) LIKE ? OR lower(i.title) LIKE ?) ORDER BY ts DESC LIMIT ?", (comp_id, like, like, limit))]
        for r in rows:
            fl = _json.loads(r.pop("flags_json") or "{}")
            r["author"] = fl.get("author") or fl.get("from") or ""
            r["text"] = "\n".join(t for (t,) in self._r.execute("SELECT text FROM chunks WHERE item_id=? ORDER BY ordinal LIMIT 3", (r["id"],)))[:600]
        return rows

    def chunk_text(self, chunk_id: str) -> str | None:
        r = self._r.execute("SELECT text FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        return r[0] if r else None

    def provenance_of(self, child_type: str, child_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._r.execute("SELECT * FROM provenance WHERE child_type=? AND child_id=?", (child_type, child_id))]

    # ---------------------------------------------------------------- vectors
    def load_embeddings(self, comp_id: str, owner_types: tuple[str, ...] = ("chunk", "event", "fact")) -> tuple[list[tuple[str, str]], np.ndarray]:
        q = ",".join("?" * len(owner_types))
        rows = self._r.execute(f"SELECT owner_type, owner_id, vec FROM embeddings WHERE compartment_id=? AND owner_type IN ({q})", (comp_id, *owner_types)).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        ids = [(r[0], r[1]) for r in rows]
        return ids, np.stack([blob_to_vec(r[2]) for r in rows])

    # ---------------------------------------------------------------- integrity
    def verify_chain(self) -> tuple[bool, int | None]:
        """Recompute the event hash chain. Returns (ok, first_bad_seq)."""
        prev = GENESIS
        for r in self._r.execute("SELECT seq, ts, compartment_id, kind, actor, content, trust, prev_hash, hash FROM events ORDER BY seq"):
            if r["prev_hash"] != prev or event_hash(prev, r["seq"], r["ts"], r["compartment_id"], r["kind"], r["actor"], r["content"], r["trust"]) != r["hash"]:
                return False, int(r["seq"])
            prev = r["hash"]
        return True, None

    def count(self, table: str) -> int:
        allowed = {"items", "chunks", "events", "facts", "embeddings", "provenance", "people", "saga", "commitments"}
        if table not in allowed:
            raise ValueError("unknown table")
        return int(self._r.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


    def user_messages(self, comp_id: str, since_ms: int, limit: int = 400) -> list[dict[str, Any]]:
        """What the user typed since a time, oldest first: the raw material for noticing habits and for replaying a day."""
        rows = self._r.execute("SELECT seq, ts, content FROM events WHERE compartment_id=? AND kind='user_message' AND ts>=? ORDER BY seq LIMIT ?", (comp_id, since_ms, limit)).fetchall()
        return [{"seq": r[0], "ts": r[1], "text": r[2]} for r in rows]

    def day_events(self, comp_id: str, since_ms: int, limit: int = 600) -> list[dict[str, Any]]:
        rows = self._r.execute("SELECT seq, ts, kind, content FROM events WHERE compartment_id=? AND ts>=? AND kind IN ('user_message','assistant_message') ORDER BY seq LIMIT ?", (comp_id, since_ms, limit)).fetchall()
        return [{"seq": r[0], "ts": r[1], "kind": r[2], "text": r[3]} for r in rows]

    def conversation_tail(self, comp_id: str, limit: int = 12) -> list[tuple[str, str]]:
        """The last turns of the chat, oldest first, to restore working memory after a restart (NFR3.2)."""
        rows = self._r.execute("SELECT kind, content FROM events WHERE compartment_id=? AND kind IN ('user_message','assistant_message') ORDER BY seq DESC LIMIT ?", (comp_id, limit)).fetchall()
        return [("user" if r[0] == "user_message" else "assistant", r[1]) for r in reversed(rows)]
