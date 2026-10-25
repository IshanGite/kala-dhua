"""Drives one connector: sync -> queue -> fetch -> ingest, with a persisted cursor and sender pairing.

The cursor is saved only after the references are queued, and the queue is idempotent on (source, item_ref), so a crash at
any point re-delivers at worst; nothing is lost and nothing is duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass

from ragnarok.connectors.base import Connector, Cursor, ItemRef
from ragnarok.core.logging import log
from ragnarok.ingest.pipeline import ingest_text
from ragnarok.llm_gateway.client import Gateway
from ragnarok.memory.connector_store import ConnectorStore
from ragnarok.memory.queue import IngestQueue
from ragnarok.memory.service import MemoryService
from ragnarok.store.vectors import VectorIndex

SEP = "|"
VER = "\x1f"  # item_ref = [sender|]native_id[\x1fversion]; a new version of the same item is a new queue key


@dataclass
class CycleReport:
    queued: int = 0
    held: int = 0
    ingested: int = 0
    unchanged: int = 0
    excluded: int = 0
    failed: int = 0
    dead: int = 0


class ConnectorRunner:
    def __init__(self, mem: MemoryService, queue: IngestQueue, connector: Connector, compartment: str, *, gateway: Gateway | None = None, index: VectorIndex | None = None, require_pairing: bool = False) -> None:
        self._m, self._q, self._c = mem, queue, connector
        self._comp, self._gw, self._idx, self._pair = compartment, gateway, index, require_pairing
        self._store = ConnectorStore(mem, connector.name)

    def load_cursor(self) -> Cursor | None:
        v = self._store.load_cursor()
        return Cursor(v) if v is not None else None

    def approve_sender(self, handle: str) -> int:
        self._store.set_pairing(handle, "approved")
        return self._q.release_held(self._c.name, handle + SEP)

    def block_sender(self, handle: str) -> None:
        self._store.set_pairing(handle, "blocked")

    async def pull(self) -> CycleReport:
        rep = CycleReport()
        cursor = self.load_cursor()
        while True:
            res = await self._c.sync(cursor)
            for ref in res.refs:
                sender = ref.hint.get("sender") if self._pair else None
                state = self._store.pairing(sender) if sender else "approved"
                if state == "blocked":
                    continue
                item_ref = f"{sender}{SEP}{ref.native_id}" if sender else ref.native_id
                if "version" in ref.hint:
                    item_ref += f"{VER}{ref.hint['version']}"
                if self._q.enqueue(self._c.name, item_ref, priority=ref.priority, held=(state == "held")):
                    rep.held += state == "held"
                    rep.queued += state != "held"
            self._store.save_cursor(res.cursor.value)
            cursor = res.cursor
            if not res.more:
                return rep

    async def drain(self, rep: CycleReport | None = None, limit: int = 50) -> CycleReport:
        rep = rep or CycleReport()
        for row in self._q.claim(limit):
            if row.source != self._c.name:
                self._q.fail(row.id, "E_WRONG_SOURCE")
                continue
            native = row.item_ref.split(VER, 1)[0]
            native = native.split(SEP, 1)[1] if (self._pair and SEP in native) else native
            try:
                raw = await self._c.fetch(ItemRef(native))
                if raw is None:
                    self._q.complete(row.id)
                    continue
                res = await ingest_text(
                    self._m, self._gw, self._idx, source=raw.source, native_id=raw.native_id, compartment=self._comp, trust=raw.trust, text=raw.text,
                    title=raw.title, kind=raw.kind, visibility=raw.visibility, thread_id=raw.thread_id, deep_link=raw.deep_link,
                    occurred_at=raw.occurred_at, html_hidden=raw.html_hidden, strip_quotes=raw.strip_quotes,
                )
                self._q.complete(row.id)
                if res.action == "unchanged":
                    rep.unchanged += 1
                elif res.action == "excluded":
                    rep.excluded += 1
                else:
                    rep.ingested += 1
            except Exception as exc:  # noqa: BLE001 - one bad item must never stall the queue
                code = getattr(exc, "code", "E_FETCH")
                log("connector_item_failed", src=self._c.name, err=code)
                rep.failed += 1
                if self._q.fail(row.id, code) == "dead":
                    rep.dead += 1
        return rep

    async def cycle(self) -> CycleReport:
        rep = await self.pull()
        return await self.drain(rep)
