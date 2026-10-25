"""Ingestion pipeline for text that is already parsed: sanitize, chunk, index, rule-extract, commit, embed.

Parsing of files happens earlier in a separate worker process (ingest/parse_worker.py). Connectors that deliver
text directly (Slack, Telegram, mail bodies) call ingest_text() straight away. One transaction commits the item and
its chunks; embeddings follow and can be retried (the keyword index is usable immediately).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ragnarok.core.clock import get_clock
from ragnarok.core.errors import ModelError
from ragnarok.core.logging import log
from ragnarok.ingest.chunk import chunk_text, embed_input
from ragnarok.ingest.extract_rules import extract, summarize
from ragnarok.ingest.sanitize import sanitize
from ragnarok.ingest.textutil import message_body
from ragnarok.llm_gateway.client import Gateway, Priority
from ragnarok.memory.models import CanonicalItem, Trust
from ragnarok.memory.service import MemoryService
from ragnarok.store.vectors import VectorIndex

EMBED_BATCH = 16


@dataclass
class IngestResult:
    item_id: str
    action: str
    chunks: int = 0
    embedded: int = 0
    flags: dict[str, Any] = field(default_factory=dict)


async def ingest_text(
    mem: MemoryService,
    gateway: Gateway | None,
    index: VectorIndex | None,
    *,
    source: str,
    native_id: str,
    compartment: str,
    trust: Trust,
    text: str,
    title: str | None = None,
    kind: str = "message",
    visibility: str = "private",
    thread_id: str | None = None,
    deep_link: str | None = None,
    occurred_at: int | None = None,
    html_hidden: bool = False,
    strip_quotes: bool = False,
    embed_model: str = "nomic-embed-text",
    priority: Priority = Priority.BACKGROUND,
    max_chunk_chars: int = 1200,
    extra_flags: dict[str, Any] | None = None,
) -> IngestResult:
    body = message_body(text) if strip_quotes else text
    san = sanitize(body, html_hidden_seen=html_hidden)
    chunks = chunk_text(san.text, max_chars=max_chunk_chars)
    flags = {**san.flags, "extracted": summarize(extract(san.text)), **{k: v for k, v in (extra_flags or {}).items() if isinstance(v, (str, int, bool)) and len(str(v)) < 300}}
    item = CanonicalItem(
        source=source, native_id=native_id, compartment=compartment, trust=trust, kind=kind, title=title,
        visibility=visibility, thread_id=thread_id, deep_link=deep_link, occurred_at=occurred_at, chunks=chunks, flags=flags,  # type: ignore[arg-type]
    )
    res = mem.write_item(item)
    if res.action in ("unchanged", "excluded"):
        return IngestResult(res.item_id, res.action, flags=flags)
    embedded = 0
    if gateway is not None and chunks:
        deadline = get_clock().monotonic() + 120
        for i in range(0, len(chunks), EMBED_BATCH):
            batch = list(zip(res.chunk_ids[i : i + EMBED_BATCH], chunks[i : i + EMBED_BATCH], strict=True))
            try:
                vecs = await gateway.embed([embed_input(c, title) for _, c in batch], deadline=deadline, priority=priority)
            except ModelError as exc:
                log("embed_deferred", code=exc.code, n=len(batch))  # keyword search already works; embeddings are retried later
                break
            for (cid, _), v in zip(batch, vecs, strict=True):
                mem.put_embedding("chunk", cid, compartment, embed_model, v)
                if index is not None:
                    index.add(compartment, ("chunk", cid), v)
                embedded += 1
    log("item_ingested", src=source, action=res.action, chunks=len(chunks), emb=embedded)
    return IngestResult(res.item_id, res.action, len(chunks), embedded, flags)
