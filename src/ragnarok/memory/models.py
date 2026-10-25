"""Boundary models for everything that crosses into or out of memory."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Trust = Literal["user_direct", "user_file", "agent", "received", "web"]
TRUST_RANK: dict[str, int] = {"user_direct": 4, "user_file": 3, "agent": 2, "received": 1, "web": 0}
LOW_TRUST = {"received", "web"}


def min_trust(*levels: str) -> str:
    """The lowest trust wins when data is merged (taint)."""
    return min(levels, key=lambda t: TRUST_RANK[t]) if levels else "user_direct"


class EventIn(BaseModel):
    compartment: str
    kind: str
    actor: str
    content: str
    trust: Trust = "user_direct"
    task_id: str | None = None
    ts: int | None = None


class ChunkIn(BaseModel):
    text: str
    heading_path: str | None = None
    page: int | None = None
    char_start: int | None = None
    char_end: int | None = None


class CanonicalItem(BaseModel):
    """What every connector and importer normalizes to (ARCHITECTURE 8.2)."""

    source: str
    native_id: str
    compartment: str
    trust: Trust
    kind: str = "message"
    title: str | None = None
    visibility: Literal["public", "private", "dm"] = "private"
    thread_id: str | None = None
    deep_link: str | None = None
    occurred_at: int | None = None
    content_hash: str | None = None
    chunks: list[ChunkIn] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)


class ProvenanceIn(BaseModel):
    parent_type: Literal["chunk", "event", "item", "fact", "user_edit"]
    parent_id: str
    relation: str = "extracted_from"
    quote: str | None = None


class FactIn(BaseModel):
    compartment: str
    kind: Literal["preference", "attribute", "relationship", "project_state", "knowledge"]
    subject: str
    predicate: str
    value: str
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    trust: Trust
    valid_from: int | None = None
    valid_to: int | None = None


class FactResult(BaseModel):
    id: str | None
    state: str
    action: Literal["inserted", "superseded_old", "needs_confirmation", "quarantined", "duplicate"]
    closed_ids: list[str] = Field(default_factory=list)


class ItemResult(BaseModel):
    item_id: str
    action: Literal["inserted", "unchanged", "replaced"]
    chunk_ids: list[str] = Field(default_factory=list)
