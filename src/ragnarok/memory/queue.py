"""Persistent ingestion queue (lives under memory/ because it writes SQL).

Stages are idempotent and the queue survives a crash: rows left 'running' are put back on boot (recover).
Live items outrank backfill (priority 1 against 5). After MAX_ATTEMPTS failures an item is dead-lettered with a
visible error code so one bad file can never stall the queue.
"""

from __future__ import annotations

from dataclasses import dataclass

from ragnarok.core.clock import get_clock
from ragnarok.core.ids import new_id
from ragnarok.memory.service import MemoryService

LIVE, BACKFILL = 1, 5
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class QueueRow:
    id: str
    source: str
    item_ref: str
    stage: str
    priority: int
    attempts: int
    payload_blob_id: str | None


class IngestQueue:
    def __init__(self, mem: MemoryService) -> None:
        self._mem = mem

    def _now(self) -> int:
        return get_clock().now_ms()

    def enqueue(self, source: str, item_ref: str, *, priority: int = BACKFILL, held: bool = False, payload_blob_id: str | None = None) -> bool:
        """Returns True if newly queued, False if (source, item_ref) was already known (idempotent delivery)."""

        def op(c) -> bool:
            now = self._now()
            cur = c.execute(
                "INSERT OR IGNORE INTO ingest_queue(id,source,item_ref,stage,state,priority,attempts,payload_blob_id,created_at,updated_at)"
                " VALUES (?,?,?,'detect',?,?,0,?,?,?)",
                (new_id(), source, item_ref, "held" if held else "queued", priority, payload_blob_id, now, now),
            )
            return cur.rowcount == 1

        return bool(self._mem.with_tx(op))

    def claim(self, limit: int = 1) -> list[QueueRow]:
        def op(c) -> list[QueueRow]:
            rows = c.execute(
                "SELECT id,source,item_ref,stage,priority,attempts,payload_blob_id FROM ingest_queue WHERE state='queued'"
                " ORDER BY priority, created_at LIMIT ?",
                (limit,),
            ).fetchall()
            now = self._now()
            for r in rows:
                c.execute("UPDATE ingest_queue SET state='running', attempts=attempts+1, updated_at=? WHERE id=?", (now, r[0]))
            return [QueueRow(r[0], r[1], r[2], r[3], r[4], r[5] + 1, r[6]) for r in rows]

        return self._mem.with_tx(op)

    def advance(self, qid: str, stage: str) -> None:
        self._mem.with_tx(lambda c: c.execute("UPDATE ingest_queue SET stage=?, updated_at=? WHERE id=?", (stage, self._now(), qid)))

    def complete(self, qid: str) -> None:
        self._mem.with_tx(lambda c: c.execute("UPDATE ingest_queue SET state='done', stage='done', updated_at=? WHERE id=?", (self._now(), qid)))

    def fail(self, qid: str, code: str) -> str:
        """Returns the new state: 'queued' (will retry) or 'dead' (dead-lettered)."""

        def op(c) -> str:
            attempts = c.execute("SELECT attempts FROM ingest_queue WHERE id=?", (qid,)).fetchone()[0]
            state = "dead" if attempts >= MAX_ATTEMPTS else "queued"
            c.execute("UPDATE ingest_queue SET state=?, last_error_code=?, updated_at=? WHERE id=?", (state, code, self._now(), qid))
            return state

        return self._mem.with_tx(op)

    def recover(self) -> int:
        """Boot: anything left 'running' by a crash goes back to the queue (stages are idempotent)."""
        return self._mem.with_tx(lambda c: c.execute("UPDATE ingest_queue SET state='queued', updated_at=? WHERE state='running'", (self._now(),)).rowcount)

    def release_held(self, source: str, ref_prefix: str) -> int:
        """The user approved a sender: release their held items."""
        return self._mem.with_tx(
            lambda c: c.execute(
                "UPDATE ingest_queue SET state='queued', updated_at=? WHERE state='held' AND source=? AND item_ref LIKE ?",
                (self._now(), source, ref_prefix + "%"),
            ).rowcount
        )

    def stats(self) -> dict[str, int]:
        def op(c) -> dict[str, int]:
            return {r[0]: r[1] for r in c.execute("SELECT state, count(*) FROM ingest_queue GROUP BY state")}

        return self._mem.with_tx(op)

    def dead_letters(self) -> list[tuple[str, str, str]]:
        return self._mem.with_tx(
            lambda c: [(r[0], r[1], r[2]) for r in c.execute("SELECT source, item_ref, coalesce(last_error_code,'') FROM ingest_queue WHERE state='dead'")]
        )
